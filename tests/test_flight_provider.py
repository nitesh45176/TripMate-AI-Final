import json
import os
import unittest
from unittest.mock import patch

from flight_provider import (
    AviationStackProvider,
    FlightProviderError,
    MockFlightProvider,
    get_flight_provider,
)


class FlightProviderTests(unittest.TestCase):
    def test_evaluation_mode_selects_deterministic_delhi_dubai_fixture(self):
        provider = get_flight_provider(eval_mode=True)

        self.assertIsInstance(provider, MockFlightProvider)
        result = provider.search("Find me a flight from Delhi to Dubai next Friday.")

        self.assertEqual(result["status"], "success")
        self.assertIn("not real flight information", result["message"])
        self.assertEqual(len(result["offers"]), 1)
        self.assertEqual(
            result["offers"][0],
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
            },
        )

    def test_evaluation_mode_does_not_construct_aviationstack_provider(self):
        with patch(
            "flight_provider.AviationStackProvider",
            side_effect=AssertionError("real provider must not be constructed"),
        ):
            provider = get_flight_provider(eval_mode=True)
            result = provider.search(
                "Find me a flight from Delhi to Dubai next Friday."
            )

        self.assertEqual(result["status"], "success")

    def test_evaluation_mode_environment_variable_selects_mock(self):
        with patch.dict(os.environ, {"TRIPMATE_EVAL_MODE": "true"}):
            provider = get_flight_provider()

        self.assertIsInstance(provider, MockFlightProvider)

    def test_mock_provider_returns_deterministic_no_results(self):
        provider = MockFlightProvider()

        first = provider.search("Find me a flight from Paris to Rome tomorrow.")
        second = provider.search("Find me a flight from Paris to Rome tomorrow.")

        self.assertEqual(first, second)
        self.assertEqual(first["status"], "no_results")
        self.assertEqual(first["offers"], [])

    def test_normal_mode_selects_aviationstack_provider(self):
        self.assertIsInstance(
            get_flight_provider(eval_mode=False),
            AviationStackProvider,
        )

    def test_aviationstack_adapter_makes_one_schedule_call_and_normalizes(self):
        calls = []

        async def fake_aviation_call(tool_name, tool_args):
            calls.append((tool_name, tool_args))
            return json.dumps(
                {
                    "ok": True,
                    "count": 2,
                    "data": [
                        {
                            "airline": "Provider Air",
                            "flight_number": "PA201",
                            "departure_scheduled_time": "2026-10-09T10:00:00+00:00",
                            "arrival_scheduled_time": "2026-10-09T13:30:00+00:00",
                            "arrival_airport_code": "DXB",
                        },
                        {
                            "airline": "Other Air",
                            "flight_number": "OA202",
                            "departure_scheduled_time": "2026-10-09T11:00:00+00:00",
                            "arrival_scheduled_time": "2026-10-09T12:00:00+00:00",
                            "arrival_airport_code": "BOM",
                        },
                    ],
                }
            )

        provider = AviationStackProvider(fake_aviation_call)
        result = provider.search(
            "Find me a flight from Delhi to Dubai on 2026-10-09."
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0],
            (
                "future_flights_arrival_departure_schedule",
                {
                    "airport_iata_code": "DEL",
                    "schedule_type": "departure",
                    "date": "2026-10-09",
                    "number_of_flights": 100,
                },
            ),
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["offers"],
            [
                {
                    "airline": "Provider Air",
                    "flight_number": "PA201",
                    "origin": "DEL",
                    "destination": "DXB",
                    "departure": "2026-10-09T10:00:00+00:00",
                    "arrival": "2026-10-09T13:30:00+00:00",
                    "duration": "3h 30m",
                    "stops": None,
                    "price": None,
                    "currency": None,
                }
            ],
        )

    def test_aviationstack_provider_errors_are_not_no_results(self):
        async def failing_aviation_call(_tool_name, _tool_args):
            raise RuntimeError("provider unavailable")

        provider = AviationStackProvider(failing_aviation_call)

        with self.assertRaisesRegex(FlightProviderError, "provider unavailable"):
            provider.search(
                "Find me a flight from Delhi to Dubai on 2026-10-09."
            )


if __name__ == "__main__":
    unittest.main()
