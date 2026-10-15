from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable, Literal, Protocol, TypedDict

import airportsdata


FlightStatus = Literal["success", "no_results", "error"]


class FlightOffer(TypedDict):
    airline: str | None
    flight_number: str | None
    origin: str | None
    destination: str | None
    departure: str | None
    arrival: str | None
    duration: str | None
    stops: int | None
    price: int | float | None
    currency: str | None


class FlightSearchResult(TypedDict):
    status: FlightStatus
    offers: list[FlightOffer]
    message: str
    error: str


class FlightProvider(Protocol):
    def search(
        self,
        query: str,
        origin: str = "",
        destination: str = "",
    ) -> FlightSearchResult: ...


class FlightProviderError(RuntimeError):
    """Raised when AviationStack or its MCP transport fails."""


AirportCall = Callable[[str, dict[str, Any]], Awaitable[Any]]

_AIRPORTS = airportsdata.load("IATA")
_ROUTE_PATTERN = re.compile(
    r"\bfrom\s+(?P<origin>.+?)\s+to\s+"
    r"(?P<destination>.+?)(?=\s+(?:on|next|this|tomorrow|today)\b|[?.!,]|$)",
    re.IGNORECASE,
)


def _parse_route(query: str) -> tuple[str, str]:
    match = _ROUTE_PATTERN.search(query)
    if not match:
        return "", ""
    return match.group("origin").strip(), match.group("destination").strip()


def _route_locations(
    query: str,
    origin: str,
    destination: str,
) -> tuple[str, str]:
    parsed_origin, parsed_destination = _parse_route(query)
    return origin or parsed_origin, destination or parsed_destination


def _airport_code(location: str) -> str | None:
    normalized = location.strip()
    if not normalized:
        return None
    code = normalized.upper()
    if code in _AIRPORTS:
        return code

    candidate = normalized.casefold()
    matches = [
        (airport_code, airport)
        for airport_code, airport in _AIRPORTS.items()
        if candidate in str(airport.get("city", "")).casefold()
        or candidate in str(airport.get("name", "")).casefold()
    ]
    if not matches:
        return None

    matches.sort(
        key=lambda entry: (
            "international" not in str(entry[1].get("name", "")).casefold(),
            entry[0],
        )
    )
    return matches[0][0]


def _travel_date(query: str) -> str:
    explicit = re.search(r"\b\d{4}-\d{2}-\d{2}\b", query)
    if explicit:
        return explicit.group(0)

    weekday_names = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    match = re.search(
        r"\b(?:(?:next|this)\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        query,
        re.IGNORECASE,
    )
    if not match:
        return ""

    today = date.today()
    days_ahead = (weekday_names[match.group(1).casefold()] - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return (today + timedelta(days=days_ahead)).isoformat()


def _duration(departure: str | None, arrival: str | None) -> str | None:
    if not departure or not arrival:
        return None
    try:
        start = datetime.fromisoformat(departure.replace("Z", "+00:00"))
        end = datetime.fromisoformat(arrival.replace("Z", "+00:00"))
    except ValueError:
        return None
    minutes = int((end - start).total_seconds() // 60)
    if minutes < 0:
        return None
    hours, remaining_minutes = divmod(minutes, 60)
    return f"{hours}h {remaining_minutes:02d}m"


def _offer(
    *,
    airline: Any = None,
    flight_number: Any = None,
    origin: Any = None,
    destination: Any = None,
    departure: Any = None,
    arrival: Any = None,
    duration: Any = None,
    stops: Any = None,
    price: Any = None,
    currency: Any = None,
) -> FlightOffer:
    return {
        "airline": airline if isinstance(airline, str) else None,
        "flight_number": flight_number if isinstance(flight_number, str) else None,
        "origin": origin if isinstance(origin, str) else None,
        "destination": destination if isinstance(destination, str) else None,
        "departure": departure if isinstance(departure, str) else None,
        "arrival": arrival if isinstance(arrival, str) else None,
        "duration": duration if isinstance(duration, str) else None,
        "stops": stops if isinstance(stops, int) else None,
        "price": price if isinstance(price, (int, float)) else None,
        "currency": currency if isinstance(currency, str) else None,
    }


def _result(
    status: FlightStatus,
    offers: list[FlightOffer],
    message: str = "",
    error: str = "",
) -> FlightSearchResult:
    return {
        "status": status,
        "offers": offers,
        "message": message,
        "error": error,
    }


class MockFlightProvider:
    """Fixed test fixtures; this provider never imports or calls an API client."""

    def search(
        self,
        query: str,
        origin: str = "",
        destination: str = "",
    ) -> FlightSearchResult:
        parsed_origin, parsed_destination = _route_locations(
            query, origin, destination
        )
        origin_code = _airport_code(parsed_origin)
        destination_code = _airport_code(parsed_destination)
        if (origin_code, destination_code) == ("DEL", "DXB"):
            return _result(
                "success",
                [
                    _offer(
                        airline="Mock Airways",
                        flight_number="MA101",
                        origin="DEL",
                        destination="DXB",
                        departure="2026-10-09T10:00:00",
                        arrival="2026-10-09T13:30:00",
                        duration="3h 30m",
                        stops=0,
                        price=320,
                        currency="USD",
                    )
                ],
                message="Evaluation-only test data; not real flight information.",
            )
        return _result(
            "no_results",
            [],
            message=f"No deterministic evaluation fixture for {origin_code or parsed_origin} "
            f"to {destination_code or parsed_destination}.",
        )


class AviationStackProvider:
    """AviationStack schedule adapter; fields not supplied by it remain unset."""

    def __init__(self, aviation_call: AirportCall | None = None):
        self._aviation_call = aviation_call

    def _call_schedule(self, args: dict[str, Any]) -> Any:
        async def call() -> Any:
            aviation_call = self._aviation_call
            if aviation_call is None:
                from mcp_client import aviation_mcp_call

                aviation_call = aviation_mcp_call
            return await aviation_call(
                "future_flights_arrival_departure_schedule",
                args,
            )

        try:
            return asyncio.run(call())
        except Exception as exc:
            raise FlightProviderError(
                f"AviationStack schedule request failed: {exc}"
            ) from exc

    def search(
        self,
        query: str,
        origin: str = "",
        destination: str = "",
    ) -> FlightSearchResult:
        parsed_origin, parsed_destination = _route_locations(
            query, origin, destination
        )
        origin_code = _airport_code(parsed_origin)
        destination_code = _airport_code(parsed_destination)
        if not origin_code or not destination_code:
            return _result(
                "no_results",
                [],
                message="Could not resolve both cities to airport codes locally.",
            )

        flight_date = _travel_date(query)
        if not flight_date:
            return _result(
                "no_results",
                [],
                message="A travel date is required for a future flight schedule search.",
            )

        response = self._call_schedule(
            {
                "airport_iata_code": origin_code,
                "schedule_type": "departure",
                "date": flight_date,
                "number_of_flights": 100,
            }
        )
        try:
            if isinstance(response, list):
                if not response:
                    raise FlightProviderError(
                        "AviationStack returned an empty MCP response."
                    )

                item = response[0]

                if isinstance(item, dict) and item.get("type") == "text":
                    response = item.get("text", "")

            payload = json.loads(response) if isinstance(response, str) else response

        except (TypeError, json.JSONDecodeError) as exc:
            raise FlightProviderError(
                "AviationStack returned a response that was not valid JSON."
            ) from exc

        if not isinstance(payload, dict):
            raise FlightProviderError("AviationStack returned an invalid response.")
        if payload.get("ok") is not True and payload.get("status") != "success":
            error = payload.get("error") or payload.get("message") or str(payload)
            raise FlightProviderError(f"AviationStack provider error: {error}")

        data = payload.get("data")
        if not isinstance(data, list):
            raise FlightProviderError("AviationStack returned an invalid flight list.")

        offers: list[FlightOffer] = []
        for flight in data:
            if not isinstance(flight, dict):
                continue
            arrival_code = flight.get("arrival_airport_code")
            if not isinstance(arrival_code, str) or arrival_code.upper() != destination_code:
                continue
            departure = flight.get("departure_scheduled_time")
            arrival = flight.get("arrival_scheduled_time")
            offers.append(
                _offer(
                    airline=flight.get("airline"),
                    flight_number=flight.get("flight_number"),
                    origin=origin_code,
                    destination=destination_code,
                    departure=departure,
                    arrival=arrival,
                    duration=_duration(departure, arrival),
                )
            )

        if not offers:
            return _result(
                "no_results",
                [],
                message=f"No scheduled flights found from {origin_code} to "
                f"{destination_code} on {flight_date}.",
            )
        return _result("success", offers)


def get_flight_provider(eval_mode: bool | None = None) -> FlightProvider:
    if eval_mode is None:
        eval_mode = os.getenv("TRIPMATE_EVAL_MODE", "").strip().casefold() in {
            "1",
            "true",
            "yes",
            "on",
        }
    return MockFlightProvider() if eval_mode else AviationStackProvider()
