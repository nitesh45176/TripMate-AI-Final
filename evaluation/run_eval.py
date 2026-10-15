
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .dataset import EVAL_DATASET
from .evaluators import dimension_statistics, evaluate_case


REPORT_DIR = Path(__file__).parent / "reports"

STATUSES = (
    "PASS",
    "FAIL",
    "ERROR",
    "EXTERNAL_SERVICE_ERROR",
    "LLM_ERROR",
)

DIMENSIONS = (
    ("guardrail", "Guardrail accuracy"),
    ("destination", "Destination accuracy"),
    ("agent_selection", "Agent selection accuracy"),
    ("response_exists", "Response existence"),
    ("flight_search", "Flight search accuracy"),
)


def _json_safe(value: Any) -> Any:
    """Convert values into JSON-serializable data."""
    return json.loads(json.dumps(value, default=str))


def _print_case(case: dict, result: dict | None, scores: dict) -> None:
    print("=" * 60)
    print(f"TEST: {case['id']}")
    print(f"Query: {case['query']}")
    print()

    labels = (
        ("guardrail", "Guardrail"),
        ("destination", "Destination"),
        ("agent_selection", "Agent Selection"),
        ("response_exists", "Response Exists"),
        ("flight_search", "Flight Search"),
    )

    for key, label in labels:
        print(f"{label + ':':<19}{scores[key]}")

        if scores[key] == "FAIL" and result is not None:
            if key == "guardrail":
                print(
                    f"{'Expected:':<19}"
                    f"{case['expected'].get('guardrail_allowed')}"
                )
                print(
                    f"{'Actual:':<19}"
                    f"{result.get('guardrail_allowed')}"
                )
            elif key == "destination":
                print(
                    f"{'Expected:':<19}"
                    f"{case['expected'].get('destination')}"
                )
                constraints = result.get("trip_constraints", {})
                print(
                    f"{'Actual:':<19}"
                    f"{constraints.get('destination')}"
                )

    print(f"{'Expected Agents:':<19}{scores['expected_agents']}")
    print(f"{'Actual Agents:':<19}{scores['actual_agents']}")
    print(f"{'Missing Agents:':<19}{scores['missing_agents']}")
    print(f"{'Unexpected Agents:':<19}{scores['unexpected_agents']}")
    print(f"{'Tool Status:':<19}{scores['tool_status']}")

    if result is not None:
        print(f"{'LLM Calls:':<19}{result.get('llm_calls', 0)}")

    for error in scores.get("tool_errors", []):
        print(f"{'Tool Error:':<19}{error.get('error', error)}")

    for error in scores.get("llm_errors", []):
        print(f"{'LLM Error:':<19}{error}")

    for field in scores.get("unvalidated_expected_fields", []):
        print(f"{'Unvalidated Field:':<19}{field}")

    if scores.get("error"):
        print(f"{'Error:':<19}{scores['error']}")

    print(f"{'Overall:':<19}{scores['overall_status']}")
    print("=" * 60)


def _build_case_report(
    case: dict,
    result: dict | None,
    scores: dict,
    llm_calls: int,
) -> dict:
    """Keep evaluation scores and actual agent output separately."""
    constraints = (
        result.get("trip_constraints", {}) if result is not None else {}
    )

    actual = {
        "guardrail_allowed": (
            result.get("guardrail_allowed") if result is not None else None
        ),
        "destination": constraints.get("destination"),
        "trip_constraints": constraints,
        "selected_agents": (
            result.get("selected_agents", []) if result is not None else []
        ),
        "response_exists": bool(
            result.get("final_response") if result is not None else False
        ),
        "llm_calls": llm_calls,
    }

    return _json_safe({
        "id": case["id"],
        "query": case["query"],
        "expected": case["expected"],
        "actual": actual,
        "scores": scores,
        "overall_status": scores["overall_status"],
        "tool_errors": scores.get("tool_errors", []),
        "llm_errors": scores.get("llm_errors", []),
        "runtime_error": scores.get("error"),
    })


def _save_report(report: dict) -> tuple[Path, Path]:
    """Write both a stable latest report and an immutable run report."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    latest_path = REPORT_DIR / "latest_report.json"
    run_path = REPORT_DIR / f"run_{timestamp}_{uuid.uuid4().hex[:6]}.json"

    serialized = json.dumps(report, indent=2, ensure_ascii=False)

    latest_path.write_text(serialized + "\n", encoding="utf-8")
    run_path.write_text(serialized + "\n", encoding="utf-8")

    return latest_path, run_path


def main() -> None:
    os.environ["TRIPMATE_EVAL_MODE"] = "true"

    case_results: list[dict] = []
    report_cases: list[dict] = []
    llm_call_counts: list[int] = []
    startup_error: Exception | None = None

    started_at = datetime.now().astimezone()

    try:
        from backend import run_travel_agent
    except Exception as exc:
        startup_error = exc
        run_travel_agent = None

    for case in EVAL_DATASET:
        result = None

        try:
            if run_travel_agent is None:
                if startup_error is None:
                    raise RuntimeError(
                        "The travel agent could not be imported."
                    )
                raise startup_error

            result = run_travel_agent(
                case["query"],
                thread_id=f"eval_{uuid.uuid4().hex}",
            )
            scores = evaluate_case(result, case["expected"])

        except Exception as exc:
            scores = evaluate_case(
                {},
                case["expected"],
                runtime_error=exc,
            )

        llm_calls = (
            result.get("llm_calls", 0) if result is not None else 0
        )

        case_results.append(scores)
        llm_call_counts.append(llm_calls)

        report_cases.append(
            _build_case_report(case, result, scores, llm_calls)
        )

        _print_case(case, result, scores)

    counts = {
        status: sum(
            item["overall_status"] == status
            for item in case_results
        )
        for status in STATUSES
    }

    statistics = dimension_statistics(case_results)

    dimension_report = {
        dimension: {
            "correct": statistics[dimension][0],
            "evaluated": statistics[dimension][1],
            "accuracy": (
                round(
                    statistics[dimension][0]
                    / statistics[dimension][1],
                    4,
                )
                if statistics[dimension][1]
                else None
            ),
        }
        for dimension, _ in DIMENSIONS
    }

    total = len(case_results)
    passed = counts["PASS"]

    report = {
        "project": "TripMate-AI",
        "generated_at": started_at.isoformat(),
        "completed_at": datetime.now().astimezone().isoformat(),
        "evaluation_mode": "deterministic mock flight provider",
        "real_aviationstack_requests": 0,
        "summary": {
            "total_cases": total,
            "passed": passed,
            "failed": counts["FAIL"],
            "errors": counts["ERROR"],
            "external_service_errors": counts["EXTERNAL_SERVICE_ERROR"],
            "llm_errors": counts["LLM_ERROR"],
            "pass_rate": round(passed / total, 4) if total else None,
            "total_llm_calls": sum(llm_call_counts),
        },
        "dimensions": dimension_report,
        "cases": report_cases,
    }

    print()
    print("Evaluation Summary")
    print("------------------")
    print("Evaluation mode: deterministic mock flight provider")
    print("Real AviationStack requests: 0")
    print(f"Total: {total}")
    print(f"LLM calls: {sum(llm_call_counts)}")

    for status in STATUSES:
        print(f"{status}: {counts[status]}")

    for dimension, label in DIMENSIONS:
        correct, evaluated = statistics[dimension]
        print(f"{label}: {correct}/{evaluated}")

    try:
        latest_path, run_path = _save_report(report)
        print()
        print("Reports saved:")
        print(f"Latest: {latest_path}")
        print(f"Run:    {run_path}")
    except OSError as exc:
        print(f"\nWARNING: Could not save evaluation report: {exc}")


if __name__ == "__main__":
    main()
