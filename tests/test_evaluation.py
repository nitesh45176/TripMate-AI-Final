import json
import unittest

from evaluation.dataset import EVAL_DATASET
from evaluation.evaluators import evaluate_case


def _flight_result():
    return json.dumps(
        {
            "status": "success",
            "offers": [
                {
                    "airline": "Mock Airways",
                    "flight_number": "MA101",
                    "origin": "DEL",
                    "destination": "DXB",
                    "departure": "2026-10-09T10:00:00",
                    "arrival": "2026-10-09T13:30:00",
                    "duration": "3h 30m",
                    "stops": 0,
                    "price": 320,
                    "currency": "USD",
                }
            ],
        }
    )


class EvaluationTests(unittest.TestCase):
    def _agent_selection_scores(
        self,
        case_id,
        actual_agents,
    ):
        case = next(case for case in EVAL_DATASET if case["id"] == case_id)
        result = {
            "guardrail_allowed": case["expected"]["guardrail_allowed"],
            "trip_constraints": {
                "destination": case["expected"]["destination"] or "",
            },
            "selected_agents": actual_agents,
            "answer": "A response is available.",
            "tool_errors": [],
            "llm_errors": [],
        }
        return evaluate_case(result, case["expected"])

    def test_existing_evaluation_cases_accept_matching_results(self):
        for case in EVAL_DATASET:
            with self.subTest(case=case["id"]):
                result = {
                    "guardrail_allowed": case["expected"]["guardrail_allowed"],
                    "trip_constraints": {
                        "destination": case["expected"]["destination"] or "",
                    },
                    "selected_agents": case["expected"]["required_agents"],
                    "answer": "A response is available.",
                    "tool_errors": [],
                    "llm_errors": [],
                }
                if case["id"] == "travel_003":
                    result["flight_results"] = _flight_result()
                if case["id"] == "travel_004":
                    result["weather_results"] = "Forecast available."

                scores = evaluate_case(result, case["expected"])

                self.assertEqual(scores["overall_status"], "PASS")

    def test_flight_evaluator_requires_normalized_offer_and_expected_route(self):
        expected = next(
            case["expected"]
            for case in EVAL_DATASET
            if case["id"] == "travel_003"
        )
        result = {
            "flight_results": _flight_result(),
            "guardrail_allowed": True,
            "trip_constraints": {"destination": "Dubai"},
            "selected_agents": ["flight_agent", "itinerary_agent"],
            "answer": "Flight schedule found.",
        }

        self.assertEqual(
            evaluate_case(result, expected)["flight_search"],
            "PASS",
        )

        result["flight_results"] = json.dumps(
            {"status": "success", "offers": [{"airline": "Made up"}]}
        )
        self.assertEqual(
            evaluate_case(result, expected)["overall_status"],
            "FAIL",
        )

    def test_result_object_alone_does_not_pass_evaluation(self):
        case = EVAL_DATASET[0]

        scores = evaluate_case({"result": "exists"}, case["expected"])

        self.assertEqual(scores["overall_status"], "FAIL")
        self.assertEqual(scores["response_exists"], "FAIL")

    def test_empty_answer_fails_response_existence(self):
        case = EVAL_DATASET[0]
        result = {
            "guardrail_allowed": True,
            "trip_constraints": {"destination": "Paris"},
            "selected_agents": case["expected"]["required_agents"],
            "answer": " \n ",
        }

        self.assertEqual(
            evaluate_case(result, case["expected"])["response_exists"],
            "FAIL",
        )

    def test_guardrail_mismatch_fails_expected_off_topic_case(self):
        case = EVAL_DATASET[-1]
        result = {
            "guardrail_allowed": True,
            "trip_constraints": {"destination": ""},
            "selected_agents": [],
            "answer": "Here is a joke.",
        }

        self.assertEqual(
            evaluate_case(result, case["expected"])["overall_status"],
            "FAIL",
        )

    def test_provider_error_is_not_a_tripmate_logic_failure(self):
        case = next(
            case for case in EVAL_DATASET if case["id"] == "travel_003"
        )
        result = {
            "guardrail_allowed": True,
            "trip_constraints": {"destination": "Dubai"},
            "selected_agents": ["flight_agent", "itinerary_agent"],
            "answer": "Flight search unavailable.",
            "flight_results": json.dumps(
                {
                    "status": "error",
                    "offers": [],
                    "error": "AviationStack unavailable",
                }
            ),
            "tool_errors": [
                {
                    "tool": "aviationstack.flight_schedule",
                    "error": "AviationStack unavailable",
                }
            ],
        }

        self.assertEqual(
            evaluate_case(result, case["expected"])["overall_status"],
            "EXTERNAL_SERVICE_ERROR",
        )

    def test_llm_error_is_not_reported_as_success(self):
        case = EVAL_DATASET[0]
        result = {
            "guardrail_allowed": True,
            "trip_constraints": {"destination": "Paris"},
            "selected_agents": case["expected"]["required_agents"],
            "answer": "Fallback response.",
            "llm_errors": ["Authentication failure from LiteLLM"],
        }

        self.assertEqual(
            evaluate_case(result, case["expected"])["overall_status"],
            "LLM_ERROR",
        )

    def test_unclassified_runtime_failure_is_reported_as_error(self):
        case = EVAL_DATASET[0]

        scores = evaluate_case(
            {},
            case["expected"],
            runtime_error=RuntimeError("Unexpected application failure"),
        )

        self.assertEqual(scores["overall_status"], "ERROR")

    def test_exact_expected_agent_set_passes(self):
        expected_agents = next(
            case["expected"]["allowed_agents"]
            for case in EVAL_DATASET
            if case["id"] == "travel_001"
        )
        scores = self._agent_selection_scores("travel_001", expected_agents)

        self.assertEqual(scores["agent_selection"], "PASS")
        self.assertEqual(scores["overall_status"], "PASS")

    def test_missing_required_agent_fails(self):
        scores = self._agent_selection_scores(
            "travel_001",
            ["budget_agent"],
        )

        self.assertEqual(scores["agent_selection"], "FAIL")
        self.assertEqual(scores["missing_agents"], ["itinerary_agent"])

    def test_unexpected_agent_fails(self):
        scores = self._agent_selection_scores(
            "travel_004",
            ["weather_agent", "itinerary_agent", "flight_agent"],
        )

        self.assertEqual(scores["agent_selection"], "FAIL")
        self.assertEqual(scores["unexpected_agents"], ["flight_agent"])

    def test_missing_and_unexpected_agents_fail(self):
        scores = self._agent_selection_scores(
            "travel_001",
            ["budget_agent", "flight_agent"],
        )

        self.assertEqual(scores["agent_selection"], "FAIL")
        self.assertEqual(scores["missing_agents"], ["itinerary_agent"])
        self.assertEqual(scores["unexpected_agents"], ["flight_agent"])

    def test_empty_expected_agent_set_rejects_actual_agents(self):
        scores = self._agent_selection_scores(
            "travel_005",
            ["flight_agent"],
        )

        self.assertEqual(scores["agent_selection"], "FAIL")
        self.assertEqual(scores["expected_agents"], [])
        self.assertEqual(scores["unexpected_agents"], ["flight_agent"])


if __name__ == "__main__":
    unittest.main()
