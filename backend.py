import os
import certifi
from dotenv import load_dotenv

load_dotenv()
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

from typing import Any, TypedDict, Annotated
import operator
import uuid
import asyncio
import json
import re
import psycopg
from psycopg.rows import dict_row
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command, interrupt
from langchain_core.messages import (
    AnyMessage,
    HumanMessage,
    AIMessage,
    SystemMessage,
)
from langchain_openai import ChatOpenAI


from mcp_client import (
    tavily_mcp_search,
    extract_destination,
    forecast_mcp_search,
    weather_mcp_search,
)
from flight_provider import FlightProviderError, get_flight_provider


def get_database_url():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise ValueError(
            "DATABASE_URL is missing. "
            "Please add your Render PostgreSQL External Database URL to .env"
        )

    if "sslmode=" not in database_url:
        separator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{separator}sslmode=require"

    return database_url


LITELLM_MASTER_KEY = os.getenv("LITELLM_MASTER_KEY")

if not LITELLM_MASTER_KEY:
    raise ValueError(
        "LITELLM_MASTER_KEY is missing. Please add it to your .env file."
    )

import time

llm = ChatOpenAI(
    model="tripmate-primary",
    api_key=LITELLM_MASTER_KEY,
    base_url="http://localhost:4000/v1",
    max_retries=0,
)


def _truncate_text(text: Any, max_chars: int = 1500) -> str:
    """Truncate text to prevent exceeding LLM token and rate limits."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = text.strip()
    if len(text) > max_chars:
        return (
            text[:max_chars]
            + f"\n... [Truncated for rate limit compliance ({len(text)} chars -> {max_chars})]"
        )
    return text


def _safe_llm_invoke(
    messages: list,
    max_tokens: int = 1500,
    max_retries: int = 5,
    invocation_counter: list[int] | None = None,
):
    """
    Invoke LLM with retry logic for Groq rate limits (413/429 TPM/RPM errors).
    """
    backoff = 2
    for attempt in range(max_retries):
        try:
            if invocation_counter is not None:
                invocation_counter[0] += 1
            return llm.invoke(messages, max_tokens=max_tokens)
        except Exception as exc:
            err_str = str(exc).lower()
            response = getattr(exc, "response", None)
            status_code = getattr(exc, "status_code", None) or getattr(
                response, "status_code", None
            )
            is_auth_error = status_code in (401, 403) or any(
                marker in err_str
                for marker in (
                    "unauthorized",
                    "authentication",
                    "invalid api key",
                    "incorrect api key",
                )
            )
            is_rate_limit_error = (
                status_code == 429
                or any(
                    marker in err_str
                    for marker in (
                        "429",
                        "rate_limit",
                        "rate limit",
                        "too many requests",
                        "tpm",
                        "rpm",
                    )
                )
            )
            if (
                not is_auth_error
                and is_rate_limit_error
                and attempt < max_retries - 1
            ):
                print(
                    f"Groq rate limit hit (attempt {attempt + 1}/{max_retries}). "
                    f"Retrying in {backoff} seconds... Error: {exc}"
                )
                time.sleep(backoff)
                backoff *= 2
            else:
                raise


def _external_tool_error(value: Any) -> str | None:
    rendered = str(value)
    normalized = rendered.casefold()
    error_markers = (
        "api_error",
        '"error":',
        "'error':",
        "function_access_restricted",
        '"ok": false',
        "'ok': false",
        '"success": false',
        "'success': false",
        "request failed",
        "temporarily unavailable",
    )
    if any(marker in normalized for marker in error_markers):
        return rendered[:1000]
    return None


# =========================
# State - original fields kept, new control fields added
# =========================
class TravelState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], operator.add]
    user_query: str

    # Supervisor + guardrail state
    guardrail_allowed: bool
    guardrail_reason: str
    selected_agents: list[str]
    trip_constraints: dict[str, Any]
    supervisor_reasoning: str

    # Original specialist results
    flight_results: str
    hotel_results: str
    weather_results: str
    itinerary: str

    # New budget + HITL state
    budget_results: str
    approval_request: str
    approved: bool
    human_feedback: str
    final_response: str

    llm_calls: int
    llm_errors: Annotated[list[str], operator.add]
    tool_errors: Annotated[list[dict[str, str]], operator.add]


# =========================
# Shared helpers
# =========================
KNOWN_AGENTS = {
    "flight_agent",
    "hotel_agent",
    "weather_agent",
    "budget_agent",
    "itinerary_agent",
}

AGENT_ORDER = [
    "flight_agent",
    "hotel_agent",
    "weather_agent",
    "budget_agent",
    "itinerary_agent",
]


def _llm_text(
    system_prompt: str,
    user_prompt: str,
    invocation_counter: list[int] | None = None,
) -> str:
    response = _safe_llm_invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ],
        max_tokens=1000,
        invocation_counter=invocation_counter,
    )
    return str(response.content)


def _json_from_llm(text: str) -> dict[str, Any]:
    """Extract the first complete JSON object returned by the model."""
    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end < start:
        raise ValueError("The model did not return a JSON object.")

    return json.loads(text[start : end + 1])


def _empty_constraints() -> dict[str, Any]:
    return {
        "destination": "",
        "origin": "",
        "duration": "",
        "budget": "",
        "travel_style": "",
        "special_preferences": [],
    }


def _query_text(query: str) -> str:
    return re.sub(r"[^a-z0-9\s]", " ", (query or "").lower()).strip()


def _rule_based_guardrail(query: str) -> bool:
    text = _query_text(query)
    if not text:
        return False

    unrelated_terms = [
        "joke",
        "funny",
        "programming",
        "story",
        "poem",
        "math",
        "science",
        "history",
        "news",
    ]
    if any(term in text for term in unrelated_terms):
        return False

    travel_terms = [
        "travel",
        "trip",
        "vacation",
        "holiday",
        "flight",
        "fly",
        "airport",
        "airline",
        "hotel",
        "stay",
        "booking",
        "accommodation",
        "lodging",
        "weather",
        "forecast",
        "temperature",
        "city",
        "budget",
        "cost",
        "price",
        "itinerary",
        "destination",
        "visit",
        "plan",
        "route",
    ]
    return any(term in text for term in travel_terms)


def _rule_based_destination(query: str) -> str:
    text = (query or "").strip()
    if not text:
        return ""

    patterns = [
        r"\bto\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)",
        r"\bin\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)",
        r"\bvisit\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)",
        r"\bfor\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidate = match.group(1).strip()
            if candidate and not candidate.lower().startswith("i "):
                return candidate

    # Heuristic fallback for common travel prompts without capitals.
    for key in ["paris", "tokyo", "dubai", "london", "delhi", "new york", "bangkok"]:
        if key in text.lower():
            return key.title()

    return ""


def _rule_based_constraints(query: str) -> dict[str, Any]:
    constraints = _empty_constraints()
    destination = _rule_based_destination(query)
    if destination:
        constraints["destination"] = destination

    budget_match = re.search(r"\$\s*(\d+(?:,\d{3})*(?:\.\d+)?)", query)
    if budget_match:
        constraints["budget"] = budget_match.group(0)

    duration_match = re.search(r"(\d+)\s*(day|days|week|weeks|night|nights)", query, flags=re.IGNORECASE)
    if duration_match:
        constraints["duration"] = f"{duration_match.group(1)} {duration_match.group(2)}"

    origin_match = re.search(r"from\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)\s+to\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)", query)
    if origin_match:
        constraints["origin"] = origin_match.group(1).strip()
        if not constraints["destination"]:
            constraints["destination"] = origin_match.group(2).strip()

    return constraints


def _rule_based_supervisor(query: str) -> list[str]:
    text = _query_text(query)
    if not _rule_based_guardrail(query):
        return []

    selected: list[str] = []
    if any(term in text for term in ["flight", "fly", "airport", "airline", "fare", "route"]):
        selected.append("flight_agent")
    if any(term in text for term in ["hotel", "stay", "accommodation", "lodging", "room", "resort"]):
        selected.append("hotel_agent")
    if any(term in text for term in ["weather", "forecast", "temperature", "rain", "climate", "sunny"]):
        selected.append("weather_agent")
    if any(term in text for term in ["budget", "cost", "price", "affordable", "cheap", "spend", "expense"]):
        selected.append("budget_agent")

    selected.append("itinerary_agent")

    return [agent for agent in AGENT_ORDER if agent in selected]


# =========================
# Supervisor Agent + Input Guardrail
# =========================
def supervisor_agent(state: TravelState):
    query = state["user_query"]
    invocation_counter = [0]

    guardrail_prompt = f"""
Determine whether the following request belongs to travel planning or travel
information. Valid requests can include destinations, flights, hotels, weather,
budgets, visas, transportation, sightseeing, food, packing, or itineraries.

Block clearly unrelated requests and requests asking for harmful or illegal
instructions. Do not block a valid travel request merely because some details
are missing.

Return strict JSON only:
{{
  "allowed": true,
  "reason": ""
}}

User request:
{query}
"""

    # Fail open on parser/model errors so a temporary JSON-format issue does not
    # break the original travel-planning behavior.
    try:
        guardrail_raw = _llm_text(
            "You are the input guardrail for a travel-planning application. "
            "Return strict JSON only.",
            guardrail_prompt,
            invocation_counter,
        )
        guardrail_result = _json_from_llm(guardrail_raw)
        allowed = bool(guardrail_result.get("allowed", True))
        guardrail_reason = str(guardrail_result.get("reason", "")).strip()
    except Exception as exc:
        print(f"Guardrail fallback used: {exc}")
        allowed = _rule_based_guardrail(query)
        guardrail_reason = (
            "Guardrail validation fallback applied deterministic routing rules."
            if allowed
            else "This request is unrelated to travel planning and was blocked."
        )
        llm_errors = [str(exc)]
    else:
        llm_errors = []

    if not allowed:
        reason = guardrail_reason or (
            "TripMate AI can only help with travel-planning requests. "
            "Please ask about a destination, flight, hotel, weather, budget, "
            "or itinerary."
        )
        return {
            "guardrail_allowed": False,
            "guardrail_reason": reason,
            "selected_agents": [],
            "trip_constraints": _empty_constraints(),
            "supervisor_reasoning": reason,
            "final_response": reason,
            "messages": [AIMessage(content=f"Guardrail blocked request: {reason}")],
            "llm_calls": state.get("llm_calls", 0) + invocation_counter[0],
            "llm_errors": llm_errors,
        }

    supervisor_prompt = f"""
You extract trip constraints and explain the travel request for a multi-agent
travel-planning system. Specialist agent selection is determined separately
from explicit request intent; do not add services merely because they could be
useful for a trip.
Populate destination, origin, duration, and budget whenever the request states
them. Do not leave a stated value blank.

Return strict JSON only using this schema:
{{
  "trip_constraints": {{
    "destination": "",
    "origin": "",
    "duration": "",
    "budget": "",
    "travel_style": "",
    "special_preferences": []
  }},
  "reasoning": ""
}}

User request:
{query}
"""

    try:
        supervisor_raw = _llm_text(
            "Extract travel constraints and reasoning. Return strict JSON only.",
            supervisor_prompt,
            invocation_counter,
        )
        parsed = _json_from_llm(supervisor_raw)
        selected_agents = _rule_based_supervisor(query)

        constraints = _rule_based_constraints(query)
        parsed_constraints = parsed.get("trip_constraints", {})
        if isinstance(parsed_constraints, dict):
            for key, value in parsed_constraints.items():
                if value is not None and (
                    not isinstance(value, str) or value.strip()
                ):
                    constraints[key] = value

        reasoning = str(parsed.get("reasoning", "")).strip()
    except Exception as exc:
        print(f"Supervisor fallback used: {exc}")
        selected_agents = _rule_based_supervisor(query)
        constraints = _rule_based_constraints(query)
        reasoning = (
            "Supervisor LLM was unavailable, so a deterministic rule-based "
            "fallback selected only the likely specialist agents for this request."
        )
        llm_errors = [str(exc)]
    else:
        llm_errors = []

    return {
        "guardrail_allowed": True,
        "guardrail_reason": guardrail_reason,
        "selected_agents": selected_agents,
        "trip_constraints": constraints,
        "supervisor_reasoning": reasoning,
        "messages": [AIMessage(content="Supervisor created the agent plan.")],
        "llm_calls": state.get("llm_calls", 0) + invocation_counter[0],
        "llm_errors": llm_errors,
    }


# =========================
# Guardrail blocked response
# =========================
def guardrail_blocked_agent(state: TravelState):
    reason = state.get("final_response") or state.get("guardrail_reason") or (
        "This request was blocked by the travel input guardrail."
    )
    return {
        "final_response": reason,
        "messages": [AIMessage(content=reason)],
    }


# =========================
# Flight Agent
# =========================
def flight_agent(state: TravelState):
    query = state["user_query"]
    constraints = state.get("trip_constraints", {}) or {}
    tool_errors: list[dict[str, str]] = []
    try:
        flight_data = get_flight_provider().search(
            query,
            origin=str(constraints.get("origin") or ""),
            destination=str(constraints.get("destination") or ""),
        )
    except FlightProviderError as exc:
        flight_data = {
            "status": "error",
            "offers": [],
            "message": "",
            "error": str(exc),
        }
        tool_errors.append(
            {"tool": "aviationstack.flight_schedule", "error": str(exc)}
        )

    return {
        "flight_results": json.dumps(flight_data, ensure_ascii=False, indent=2),
        "messages": [AIMessage(content="Flight schedule search completed.")],
        "llm_calls": state.get("llm_calls", 0),
        "tool_errors": tool_errors,
        "llm_errors": [],
    }


# =========================
# Hotel Agent - original behavior kept
# =========================
def hotel_agent(state: TravelState):
    query = (
        f"Best hotels for "
        f"{state['user_query']}"
    )

    try:
        raw_hotel_results = asyncio.run(
            tavily_mcp_search(query)
        )
        hotel_results = _truncate_text(raw_hotel_results, 1500)
        error = _external_tool_error(raw_hotel_results)
        tool_errors = (
            [{"tool": "tavily.tavily_search", "error": error}]
            if error
            else []
        )

    except Exception as exc:
        print(
            f"HOTEL AGENT MCP ERROR: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

        hotel_results = (
            "Live hotel search is temporarily unavailable. "
            "Provide general accommodation and neighborhood "
            "guidance based on the destination and clearly "
            "label it as non-live advice."
        )
        tool_errors = [
            {"tool": "tavily.tavily_search", "error": str(exc)}
        ]

    return {
        "hotel_results": hotel_results,
        "messages": [
            AIMessage(
                content="Hotel information processed."
            )
        ],
        "tool_errors": tool_errors,
        "llm_errors": [],
    }


# =========================
# Weather Agent - original behavior kept
# =========================
def weather_agent(state: TravelState):
    invocation_counter = [0]
    try:
        city = extract_destination(
            state["user_query"],
            invocation_counter=invocation_counter,
        )
    except Exception as exc:
        return {
            "weather_results": "Weather information could not be retrieved.",
            "llm_calls": state.get("llm_calls", 0) + invocation_counter[0],
            "tool_errors": [],
            "llm_errors": [str(exc)],
        }

    try:
        weather_data = asyncio.run(
            weather_mcp_search(city)
        )

        forecast_data = asyncio.run(
            forecast_mcp_search(city)
        )
        tool_errors = []
        for tool_name, tool_result in (
            ("get_current_weather", weather_data),
            ("get_forecast", forecast_data),
        ):
            error = _external_tool_error(tool_result)
            if error:
                tool_errors.append(
                    {"tool": f"weather.{tool_name}", "error": error}
                )

        weather_results = f"""
Current Weather:
{_truncate_text(weather_data, 500)}

Forecast:
{_truncate_text(forecast_data, 500)}
"""

    except Exception as exc:
        print(
            f"WEATHER AGENT MCP ERROR: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

        weather_results = (
            f"Live weather information for {city} "
            "is temporarily unavailable. Give general "
            "seasonal guidance and advise the traveler "
            "to verify the forecast before departure."
        )
        tool_errors = [
            {"tool": "weather", "error": str(exc)}
        ]

    return {
        "weather_results": weather_results,
        "messages": [
            AIMessage(
                content="Weather information processed."
            )
        ],
        "llm_calls": state.get("llm_calls", 0) + invocation_counter[0],
        "tool_errors": tool_errors,
        "llm_errors": [],
    }


# =========================
# Budget Agent - new specialist
# =========================
def budget_agent(state: TravelState):
    invocation_counter = [0]
    prompt = f"""
Analyze whether this trip is realistic for the user's budget.

User Query:
{state['user_query']}

Trip Constraints:
{state.get('trip_constraints', {})}

Flight Results:
{_truncate_text(state.get('flight_results', ''), 1000)}

Hotel Results:
{_truncate_text(state.get('hotel_results', ''), 1200)}

Weather Results:
{_truncate_text(state.get('weather_results', ''), 600)}

Return:
1. Estimated cost categories
2. Budget risk areas
3. Money-saving suggestions
4. Overall feasibility

If exact live prices are unavailable, clearly label estimates as approximate.
"""

    try:
        response = _safe_llm_invoke(
            [
                SystemMessage(content="You are a practical travel budget analyst."),
                HumanMessage(content=prompt),
            ],
            max_tokens=1200,
            invocation_counter=invocation_counter,
        )
        budget_result = str(response.content)
    except Exception as exc:
        print(f"Budget fallback used: {exc}")
        llm_errors = [str(exc)]
        constraints = state.get("trip_constraints", {}) or _empty_constraints()
        destination = constraints.get("destination") or _rule_based_destination(state["user_query"])
        budget_result = (
            f"Budget estimate for {destination or 'this trip'} is approximate. "
            "The requested trip should be reviewed for accommodation, transport, and daily spending. "
            "Use a conservative buffer of 10-20% above the base estimate to account for seasonal pricing and extras."
        )
    else:
        llm_errors = []

    return {
        "budget_results": budget_result,
        "messages": [AIMessage(content="Budget assessment generated.")],
        "llm_calls": state.get("llm_calls", 0) + invocation_counter[0],
        "llm_errors": llm_errors,
    }


# =========================
# Itinerary Agent - original behavior extended with selected results
# =========================
def itinerary_agent(state: TravelState):
    invocation_counter = [0]
    prompt = f"""
Create a complete travel itinerary.

User Query:
{state['user_query']}

Trip Constraints:
{state.get('trip_constraints', {})}

Flight Results:
{_truncate_text(state.get('flight_results', ''), 1000)}

Hotel Results:
{_truncate_text(state.get('hotel_results', ''), 1200)}

Weather Results:
{_truncate_text(state.get('weather_results', ''), 600)}

Budget Results:
{_truncate_text(state.get('budget_results', ''), 1000)}

Make the itinerary practical, budget-aware, and easy to follow.
Create a clear draft that is ready for human review.
"""

    try:
        response = _safe_llm_invoke(
            [
                SystemMessage(content="You are an expert travel planner."),
                HumanMessage(content=prompt),
            ],
            max_tokens=1500,
            invocation_counter=invocation_counter,
        )
        itinerary_text = str(response.content)
    except Exception as exc:
        print(f"Itinerary fallback used: {exc}")
        llm_errors = [str(exc)]
        destination = (state.get("trip_constraints", {}) or _empty_constraints()).get("destination") or _rule_based_destination(state["user_query"])
        duration = (state.get("trip_constraints", {}) or _empty_constraints()).get("duration") or "3-5 days"
        budget = (state.get("trip_constraints", {}) or _empty_constraints()).get("budget") or "budget-conscious"
        itinerary_text = (
            f"Draft itinerary for {destination or 'your destination'} ({duration}):\n"
            "- Day 1: Arrive, check in, and settle into the area.\n"
            "- Day 2: Explore the main landmarks and local food spots.\n"
            "- Day 3: Add a key attraction or guided activity.\n"
            "- Day 4: Keep time for rest, shopping, or optional excursions.\n"
            f"- Budget note: Keep spending aligned with {budget}.\n"
            "This draft is ready for review and can be refined with pricing, timing, and personal preferences."
        )
    else:
        llm_errors = []

    approval_request = (
        "Please review the generated draft itinerary. Approve it to create the "
        "final polished plan, or provide feedback for revision."
    )

    return {
        "itinerary": itinerary_text,
        "approval_request": approval_request,
        "messages": [AIMessage(content="Draft itinerary created for human review.")],
        "llm_calls": state.get("llm_calls", 0) + invocation_counter[0],
        "llm_errors": llm_errors,
    }


# =========================
# Human-in-the-Loop approval
# =========================
def human_approval_agent(state: TravelState):
    # Do not wrap interrupt() in try/except. LangGraph uses it to pause execution.
    review = interrupt(
        {
            "question": "Do you approve this itinerary?",
            "draft_itinerary": state.get("itinerary", ""),
            "approval_request": state.get("approval_request", ""),
            "selected_agents": state.get("selected_agents", []),
            "supervisor_reasoning": state.get("supervisor_reasoning", ""),
            "expected_response": {
                "approved": True,
                "feedback": "Optional revision feedback",
            },
        }
    )

    approved = bool(review.get("approved", False))
    human_feedback = str(review.get("feedback", "")).strip()

    return {
        "approved": approved,
        "human_feedback": human_feedback,
        "messages": [AIMessage(content="Human approval step completed.")],
    }


# =========================
# Final Response Agent - original format kept, HITL feedback added
# =========================
def final_agent(state: TravelState):
    invocation_counter = [0]
    if state.get("approved", False):
        review_instruction = (
            "The user approved the draft. Preserve its decisions while polishing it."
        )
    else:
        review_instruction = f"""
The user requested a revision. Apply this feedback carefully:
{state.get('human_feedback', '') or 'Improve the draft before finalizing it.'}
"""

    final_prompt = f"""
Generate the final travel response for the user.

Human Review:
{review_instruction}

User Request:
{state['user_query']}

Supervisor Constraints:
{state.get('trip_constraints', {})}

Flights:
{_truncate_text(state.get('flight_results', ''), 1000)}

Hotels:
{_truncate_text(state.get('hotel_results', ''), 1200)}

Weather:
{_truncate_text(state.get('weather_results', ''), 600)}

Budget Analysis:
{_truncate_text(state.get('budget_results', ''), 1000)}

Draft Itinerary:
{_truncate_text(state.get('itinerary', ''), 1500)}

Format the final answer beautifully using these sections:
1. Trip Summary
2. Flight Information
3. Hotel Suggestions
4. Weather Information
5. Day-by-Day Itinerary
6. Estimated Budget
7. Final Recommendations

Important:
- Be clear and practical.
- Mention that live flight APIs may not provide ticket prices when pricing is unavailable.
- Include weather-based travel advice.
- Keep the response useful for real travel planning.
- Incorporate the human feedback when revision was requested.
"""

    try:
        response = _safe_llm_invoke(
            [
                SystemMessage(
                    content="You are a professional AI travel booking assistant."
                ),
                HumanMessage(content=final_prompt),
            ],
            max_tokens=2000,
            invocation_counter=invocation_counter,
        )
        final_text = str(response.content)
    except Exception as exc:
        print(f"Final agent fallback used: {exc}")
        llm_errors = [str(exc)]
        final_text = (
            f"Trip plan summary for: {state['user_query']}\n\n"
            f"- Destination: {state.get('trip_constraints', {}).get('destination') or _rule_based_destination(state['user_query']) or 'Not specified'}\n"
            f"- Selected agents: {state.get('selected_agents', [])}\n"
            f"- Draft itinerary: {state.get('itinerary', 'A practical itinerary draft is ready for review.')}\n\n"
            "Review the itinerary and refine any costs, flights, or accommodation details before booking."
        )
    else:
        llm_errors = []

    return {
        "final_response": final_text,
        "messages": [AIMessage(content=final_text)],
        "llm_calls": state.get("llm_calls", 0) + invocation_counter[0],
        "llm_errors": llm_errors,
    }


# =========================
# Dynamic Supervisor Routing
# =========================
ROUTE_MAP = {
    "guardrail_blocked": "guardrail_blocked",
    "flight_agent": "flight_agent",
    "hotel_agent": "hotel_agent",
    "weather_agent": "weather_agent",
    "budget_agent": "budget_agent",
    "itinerary_agent": "itinerary_agent",
}


def _selected_agents(state: TravelState) -> list[str]:
    selected = state.get("selected_agents", [])
    return [agent for agent in AGENT_ORDER if agent in selected]


def route_from_supervisor(state: TravelState) -> str:
    if not state.get("guardrail_allowed", True):
        return "guardrail_blocked"

    selected = _selected_agents(state)
    return selected[0] if selected else "itinerary_agent"


def route_after_agent(current_agent: str):
    def route(state: TravelState) -> str:
        selected = _selected_agents(state)
        current_index = AGENT_ORDER.index(current_agent)

        for next_agent in AGENT_ORDER[current_index + 1 :]:
            if next_agent in selected:
                return next_agent

        return "itinerary_agent"

    return route


# =========================
# Build Graph
# =========================
graph = StateGraph(TravelState)

graph.add_node("supervisor", supervisor_agent)
graph.add_node("guardrail_blocked", guardrail_blocked_agent)
graph.add_node("flight_agent", flight_agent)
graph.add_node("hotel_agent", hotel_agent)
graph.add_node("weather_agent", weather_agent)
graph.add_node("budget_agent", budget_agent)
graph.add_node("itinerary_agent", itinerary_agent)
graph.add_node("human_approval", human_approval_agent)
graph.add_node("final_agent", final_agent)

graph.add_edge(START, "supervisor")
graph.add_conditional_edges("supervisor", route_from_supervisor, ROUTE_MAP)

graph.add_conditional_edges(
    "flight_agent", route_after_agent("flight_agent"), ROUTE_MAP
)
graph.add_conditional_edges(
    "hotel_agent", route_after_agent("hotel_agent"), ROUTE_MAP
)
graph.add_conditional_edges(
    "weather_agent", route_after_agent("weather_agent"), ROUTE_MAP
)
graph.add_conditional_edges(
    "budget_agent", route_after_agent("budget_agent"), ROUTE_MAP
)

graph.add_edge("itinerary_agent", "human_approval")
graph.add_edge("human_approval", "final_agent")
graph.add_edge("final_agent", END)
graph.add_edge("guardrail_blocked", END)



# =========================
# PostgreSQL Checkpointer - original persistence kept
# =========================
DATABASE_URL = get_database_url()
_conn = psycopg.connect(
    DATABASE_URL,
    autocommit=True,
    row_factory=dict_row,
)
checkpointer = PostgresSaver(_conn)
checkpointer.setup()

travel_graph = graph.compile(checkpointer=checkpointer)


# =========================
# FastAPI-facing helpers
# =========================
def _interrupt_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    interrupts = result.get("__interrupt__", [])
    if not interrupts:
        return None

    first_interrupt = interrupts[0]
    payload = getattr(first_interrupt, "value", first_interrupt)
    return payload if isinstance(payload, dict) else {"value": payload}


def _serialize_result(
    result: dict[str, Any],
    thread_id: str,
) -> dict[str, Any]:
    messages = result.get("messages", [])
    last_message = messages[-1].content if messages else ""
    answer = result.get("final_response") or last_message
    interrupt_payload = _interrupt_payload(result)

    if interrupt_payload:
        answer = interrupt_payload.get("draft_itinerary") or result.get(
            "itinerary", ""
        )

    return {
        "thread_id": thread_id,
        "answer": answer,
        "requires_approval": interrupt_payload is not None,
        "approval_request": (
            interrupt_payload.get("approval_request", "")
            if interrupt_payload
            else result.get("approval_request", "")
        ),
        "flight_results": result.get("flight_results", ""),
        "hotel_results": result.get("hotel_results", ""),
        "weather_results": result.get("weather_results", ""),
        "budget_results": result.get("budget_results", ""),
        "itinerary": (
            interrupt_payload.get("draft_itinerary", "")
            if interrupt_payload
            else result.get("itinerary", "")
        ),
        "selected_agents": result.get("selected_agents", []),
        "trip_constraints": result.get("trip_constraints", {}),
        "supervisor_reasoning": result.get("supervisor_reasoning", ""),
        "guardrail_allowed": result.get("guardrail_allowed", True),
        "guardrail_reason": result.get("guardrail_reason", ""),
        "approved": result.get("approved"),
        "human_feedback": result.get("human_feedback", ""),
        "llm_calls": result.get("llm_calls", 0),
        "llm_errors": result.get("llm_errors", []),
        "tool_errors": result.get("tool_errors", []),
    }


def run_travel_agent(user_input: str, thread_id: str | None = None):
    """Start a new travel-planning run and pause at human approval."""
    if not thread_id:
        thread_id = f"user_{uuid.uuid4().hex}"

    config = {"configurable": {"thread_id": thread_id}}

    result = travel_graph.invoke(
        {
            "messages": [HumanMessage(content=user_input)],
            "user_query": user_input,
            "guardrail_allowed": True,
            "guardrail_reason": "",
            "selected_agents": [],
            "trip_constraints": _empty_constraints(),
            "supervisor_reasoning": "",
            "flight_results": "",
            "hotel_results": "",
            "weather_results": "",
            "budget_results": "",
            "itinerary": "",
            "approval_request": "",
            "approved": False,
            "human_feedback": "",
            "final_response": "",
            "llm_calls": 0,
            "llm_errors": [],
            "tool_errors": [],
        },
        config=config,
    )

    return _serialize_result(result, thread_id)


def resume_travel_agent(
    thread_id: str,
    approved: bool,
    feedback: str = "",
):
    """Resume the paused LangGraph thread after human review."""
    if not thread_id:
        raise ValueError("thread_id is required to resume a travel plan.")

    config = {"configurable": {"thread_id": thread_id}}
    result = travel_graph.invoke(
        Command(
            resume={
                "approved": approved,
                "feedback": feedback.strip(),
            }
        ),
        config=config,
    )

    return _serialize_result(result, thread_id)