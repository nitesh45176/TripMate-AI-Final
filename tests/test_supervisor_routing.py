import json
import unittest
from unittest.mock import patch

from backend import supervisor_agent
from evaluation.dataset import EVAL_DATASET


class SupervisorRoutingTests(unittest.TestCase):
    def test_successful_supervisor_uses_query_intent_not_llm_agent_list(self):
        llm_overselect = json.dumps(
            {
                "selected_agents": [
                    "flight_agent",
                    "hotel_agent",
                    "weather_agent",
                    "budget_agent",
                    "itinerary_agent",
                ],
                "trip_constraints": {},
                "reasoning": "Mocked over-selection.",
            }
        )

        for case in EVAL_DATASET[:4]:
            with self.subTest(case=case["id"]):
                guardrail = json.dumps({"allowed": True, "reason": ""})
                with patch(
                    "backend._llm_text",
                    side_effect=[guardrail, llm_overselect],
                ):
                    result = supervisor_agent(
                        {
                            "user_query": case["query"],
                            "llm_calls": 0,
                        }
                    )

                self.assertEqual(
                    set(result["selected_agents"]),
                    set(case["expected"]["allowed_agents"]),
                )

    def test_guardrail_blocked_query_selects_no_specialists(self):
        case = EVAL_DATASET[-1]
        guardrail = json.dumps(
            {"allowed": False, "reason": "Unrelated to travel."}
        )

        with patch("backend._llm_text", return_value=guardrail):
            result = supervisor_agent(
                {
                    "user_query": case["query"],
                    "llm_calls": 0,
                }
            )

        self.assertFalse(result["guardrail_allowed"])
        self.assertEqual(result["selected_agents"], [])


if __name__ == "__main__":
    unittest.main()
