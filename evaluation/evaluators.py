from __future__ import annotations

import json
from typing import Any

DIMENSIONS = (
    "guardrail",
    "destination",
    "agent_selection",
    "response_exists",
    "flight_search",
)

EXTERNAL_TOOL_AGENTS = {
    "flight_agent",
    "hotel_agent",
    "weather_agent",
}

AGENT_RESULT_FIELDS = {
    "flight_agent": "flight_results",
    "hotel_agent": "hotel_results",
    "weather_agent": "weather_results",
}


def _status(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def _check_guardrail(result: dict[str, Any], expected: dict[str, Any]) -> bool:
    if "guardrail_allowed" not in expected:
        return False
    return (
        "guardrail_allowed" in result
        and result["guardrail_allowed"] is expected["guardrail_allowed"]
    )


def _check_destination(result: dict[str, Any], expected: dict[str, Any]) -> bool:
    if "destination" not in expected:
        return False

    constraints = result.get("trip_constraints")
    if not isinstance(constraints, dict) or "destination" not in constraints:
        return False

    actual = constraints["destination"]
    expected_destination = expected["destination"]
    if expected_destination is None:
        destination_passed = actual is None or (
            isinstance(actual, str) and not actual.strip()
        )
    else:
        destination_passed = (
            isinstance(actual, str)
            and str(expected_destination).casefold() in actual.casefold()
        )

    expected_constraints = expected.get("expected_constraints", {})
    if not isinstance(expected_constraints, dict):
        return False
    for key, expected_value in expected_constraints.items():
        if key not in constraints:
            return False
        actual_value = constraints[key]
        if expected_value is None:
            if actual_value is not None and not (
                isinstance(actual_value, str) and not actual_value.strip()
            ):
                return False
        elif isinstance(expected_value, str):
            if (
                not isinstance(actual_value, str)
                or expected_value.casefold() not in actual_value.casefold()
            ):
                return False
        elif actual_value != expected_value:
            return False
    return destination_passed


def _check_agent_selection(
    result: dict[str, Any],
    expected: dict[str, Any],
) -> bool:
    if "required_agents" not in expected:
        return False

    actual_agents = result.get("selected_agents")
    if not isinstance(actual_agents, (list, tuple, set)):
        return False
    required_agents = set(expected["required_agents"])
    selected_agents = set(actual_agents)
    missing_agents = required_agents - selected_agents

    if missing_agents:
        return False

    if "allowed_agents" not in expected:
        return True
    allowed_agents = expected["allowed_agents"]
    if not isinstance(allowed_agents, (list, tuple, set)):
        return False
    return selected_agents.issubset(set(allowed_agents))


def _agent_selection_details(
    result: dict[str, Any],
    expected: dict[str, Any],
) -> dict[str, list[str]]:
    required_agents = set(expected.get("required_agents", []))
    allowed_agents = expected.get("allowed_agents")
    expected_agents = (
        set(allowed_agents)
        if isinstance(allowed_agents, (list, tuple, set))
        else required_agents
    )
    selected_agents = result.get("selected_agents", [])
    actual_agents = (
        set(selected_agents)
        if isinstance(selected_agents, (list, tuple, set))
        else set()
    )
    return {
        "expected_agents": sorted(expected_agents),
        "actual_agents": sorted(actual_agents),
        "missing_agents": sorted(required_agents - actual_agents),
        "unexpected_agents": (
            sorted(actual_agents - expected_agents)
            if "allowed_agents" in expected
            else []
        ),
    }


def _check_response_exists(result: dict[str, Any]) -> bool:
    answer = result.get("answer")
    return isinstance(answer, str) and bool(answer.strip())


def _check_flight_search(
    result: dict[str, Any],
    expected: dict[str, Any],
) -> bool:
    flight_results = result.get("flight_results")
    if isinstance(flight_results, str):
        try:
            flight_results = json.loads(flight_results)
        except json.JSONDecodeError:
            return False
    if not isinstance(flight_results, dict):
        return False

    expected_flight = expected["expected_flight"]
    if flight_results.get("status") != expected_flight.get("status", "success"):
        return False
    offers = flight_results.get("offers")
    if not isinstance(offers, list) or len(offers) < expected_flight.get(
        "minimum_offers", 1
    ):
        return False

    expected_origin = expected_flight.get("origin")
    expected_destination = expected_flight.get("destination")
    required_fields = {
        "airline",
        "flight_number",
        "origin",
        "destination",
        "departure",
        "arrival",
        "duration",
        "stops",
        "price",
        "currency",
    }
    for offer in offers:
        if not isinstance(offer, dict) or not required_fields.issubset(offer):
            return False
        if expected_origin and offer.get("origin") != expected_origin:
            return False
        if expected_destination and offer.get("destination") != expected_destination:
            return False
    return True


def _classify_error(error: Any) -> str:
    text = str(error).casefold()
    if any(term in text for term in (
        "401",
        "403",
        "unauthorized",
        "authentication",
        "invalid api key",
        "llm",
        "litellm",
        "groq",
        "model provider",
    )):
        return "LLM_ERROR"
    if any(term in text for term in (
        "api_error",
        "function_access_restricted",
        "external service",
        "aviationstack",
        "openweather",
        "tavily",
        "tool call",
        "temporarily unavailable",
    )):
        return "EXTERNAL_SERVICE_ERROR"
    return "ERROR"


def _tool_status(
    result: dict[str, Any],
    expected: dict[str, Any],
) -> tuple[str, list[dict[str, str]]]:
    required = set(expected.get("required_agents", []))
    applicable_agents = required & EXTERNAL_TOOL_AGENTS
    selected_agents = result.get("selected_agents", [])
    selected = set(selected_agents) if isinstance(
        selected_agents, (list, tuple, set)
    ) else set()
    errors = result.get("tool_errors", [])
    if errors:
        return "EXTERNAL_SERVICE_ERROR", errors

    tested_agents = applicable_agents & selected
    if not applicable_agents:
        tested_agents = selected & EXTERNAL_TOOL_AGENTS
    if tested_agents:
        missing_results = [
            agent
            for agent in tested_agents
            if not result.get(AGENT_RESULT_FIELDS[agent])
        ]
        if missing_results:
            return "FAIL", [
                {
                    "tool": agent,
                    "error": "The selected agent returned no result.",
                }
                for agent in sorted(missing_results)
            ]
        return "PASS", []

    return "NOT_TESTED", []


def evaluate_case(
    result: dict[str, Any],
    expected: dict[str, Any],
    runtime_error: Exception | str | None = None,
) -> dict[str, Any]:
    if runtime_error is not None:
        error_status = _classify_error(runtime_error)
        return {
            **{dimension: "ERROR" for dimension in DIMENSIONS},
            "tool_status": "NOT_TESTED",
            "tool_errors": [],
            "expected_agents": sorted(
                expected.get("allowed_agents", expected.get("required_agents", []))
            ),
            "actual_agents": [],
            "missing_agents": sorted(expected.get("required_agents", [])),
            "unexpected_agents": [],
            "overall_status": error_status,
            "error": str(runtime_error),
        }

    guardrail_passed = _check_guardrail(result, expected)
    destination_passed = _check_destination(result, expected)
    agents_passed = _check_agent_selection(result, expected)
    agent_details = _agent_selection_details(result, expected)
    response_passed = _check_response_exists(result)
    tool_status, tool_errors = _tool_status(result, expected)
    flight_search_status = (
        _status(_check_flight_search(result, expected))
        if "expected_flight" in expected
        else "NOT_TESTED"
    )
    unsupported_expected_fields = sorted(
        set(expected)
        - {
            "guardrail_allowed",
            "destination",
            "expected_constraints",
            "required_agents",
            "allowed_agents",
            "expected_flight",
        }
    )

    llm_errors = result.get("llm_errors", [])
    if llm_errors:
        overall_status = "LLM_ERROR"
    elif tool_status == "EXTERNAL_SERVICE_ERROR":
        overall_status = "EXTERNAL_SERVICE_ERROR"
    elif tool_status == "FAIL":
        overall_status = "FAIL"
    elif unsupported_expected_fields or not all((
        guardrail_passed,
        destination_passed,
        agents_passed,
        response_passed,
    )) or flight_search_status == "FAIL":
        overall_status = "FAIL"
    else:
        overall_status = "PASS"

    return {
        "guardrail": _status(guardrail_passed),
        "destination": _status(destination_passed),
        "agent_selection": _status(agents_passed),
        **agent_details,
        "response_exists": _status(response_passed),
        "flight_search": flight_search_status,
        "tool_status": tool_status,
        "tool_errors": tool_errors,
        "overall_status": overall_status,
        "llm_errors": llm_errors,
        "unvalidated_expected_fields": unsupported_expected_fields,
    }


def dimension_statistics(
    results: list[dict[str, Any]],
) -> dict[str, tuple[int, int]]:
    return {
        dimension: (
            sum(result.get(dimension) == "PASS" for result in results),
            sum(result.get(dimension) in {"PASS", "FAIL"} for result in results),
        )
        for dimension in DIMENSIONS
    }
