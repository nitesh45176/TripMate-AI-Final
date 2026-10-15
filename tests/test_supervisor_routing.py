import json
import unittest
from unittest.mock import MagicMock, patch

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from backend import (
    _safe_llm_invoke,
    final_agent,
    graph,
    route_from_supervisor,
    supervisor_agent,
)
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

    def test_supervisor_failure_preserves_japan_for_fallback_and_final(self):
        guardrail = json.dumps({"allowed": True, "reason": ""})
        with patch(
            "backend._llm_text",
            side_effect=[guardrail, ConnectionError("gateway unavailable")],
        ):
            result = supervisor_agent(
                {
                    "user_query": "Japan trip for 7 days.",
                    "llm_calls": 0,
                }
            )

        self.assertEqual(
            result["trip_constraints"]["destination"],
            "Japan",
        )
        self.assertEqual(result["selected_agents"], ["itinerary_agent"])
        self.assertEqual(route_from_supervisor(result), "itinerary_agent")
        self.assertTrue(result["llm_errors"])

        workflow = graph.compile(checkpointer=MemorySaver())
        draft = MagicMock(content="Draft itinerary for Japan.")
        with (
            patch(
                "backend._llm_text",
                side_effect=[
                    guardrail,
                    ConnectionError("gateway unavailable"),
                ],
            ),
            patch("backend._safe_llm_invoke", return_value=draft),
        ):
            graph_result = workflow.invoke(
                {
                    "messages": [HumanMessage(content="Japan trip for 7 days.")],
                    "user_query": "Japan trip for 7 days.",
                },
                {"configurable": {"thread_id": "japan-destination-test"}},
            )

        self.assertEqual(
            graph_result["trip_constraints"]["destination"],
            "Japan",
        )
        self.assertIn(
            "Draft itinerary for Japan.",
            graph_result["__interrupt__"][0].value["draft_itinerary"],
        )

        with patch(
            "backend._safe_llm_invoke",
            side_effect=ConnectionError("gateway unavailable"),
        ):
            final = final_agent(
                {
                    "user_query": "Japan trip for 7 days.",
                    "trip_constraints": result["trip_constraints"],
                    "selected_agents": result["selected_agents"],
                }
            )

        self.assertIn("Destination: Japan", final["final_response"])

    def test_safe_llm_invoke_retries_rate_limit_but_not_connection_failure(self):
        response = object()
        gateway = MagicMock()
        gateway.invoke.side_effect = [
            RuntimeError("429 rate limit"),
            response,
        ]
        with (
            patch(
                "backend.llm",
                gateway,
            ),
            patch("backend.time.sleep") as sleep,
        ):
            result = _safe_llm_invoke([])

        self.assertIs(result, response)
        self.assertEqual(gateway.invoke.call_count, 2)
        sleep.assert_called_once_with(2)

        gateway = MagicMock()
        gateway.invoke.side_effect = ConnectionError("gateway unavailable")
        with patch("backend.llm", gateway):
            with self.assertRaisesRegex(ConnectionError, "gateway unavailable"):
                _safe_llm_invoke([])


if __name__ == "__main__":
    unittest.main()
