import os
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, quote_plus, urlsplit

import certifi
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.client import MultiServerMCPClient


# =========================================================
# Environment setup
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

# Support both environment-variable names.
AVIATION_STACK_API_KEY = (
    os.getenv("AVIATION_STACK_API_KEY")
    or os.getenv("AVIATIONSTACK_API_KEY")
)

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")

WEATHER_SERVER_PATH = (
    BASE_DIR / "custom_weather_mcp_server.py"
    if (BASE_DIR / "custom_weather_mcp_server.py").is_file()
    else BASE_DIR / "weather_mcp_server.py"
)

UVX_COMMAND = shutil.which("uvx") or "uvx"


def _require_env(name: str, value: str | None) -> str:
    """Return an environment value or raise a readable setup error."""

    if not value or not value.strip():
        raise RuntimeError(
            f"{name} is missing. Set it in the service environment "
            "or add it to your local .env file."
        )

    return value


def safe_error_message(error: Exception) -> str:
    """Render exception details without exposing environment credentials."""

    message = str(error)
    for name, value in os.environ.items():
        if not value or not any(
            marker in name.upper()
            for marker in ("_KEY", "TOKEN", "SECRET", "PASSWORD", "DATABASE_URL")
        ):
            continue
        for secret in {value, quote(value, safe=""), quote_plus(value)}:
            if secret:
                message = message.replace(secret, "[REDACTED]")

    return f"{type(error).__name__}: {message}"


def _subprocess_env(**updates: str | None) -> dict[str, str]:
    """
    Preserve the current Windows/Conda environment and add MCP API keys.
    """

    env = os.environ.copy()

    for key, value in updates.items():
        if value:
            env[key] = value

    return env


# =========================================================
# LLM - LiteLLM Gateway
# =========================================================

def create_llm_client(
    environ: Mapping[str, str] | None = None,
) -> ChatOpenAI:
    """Build the shared OpenAI-compatible client for the LiteLLM gateway."""

    settings = os.environ if environ is None else environ
    master_key = _require_env(
        "LITELLM_MASTER_KEY",
        settings.get("LITELLM_MASTER_KEY"),
    )
    base_url = settings.get(
        "LITELLM_BASE_URL",
        "http://127.0.0.1:4000/v1",
    ).strip().rstrip("/")
    parsed_url = urlsplit(base_url)
    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.netloc
        or parsed_url.path.rstrip("/") != "/v1"
    ):
        raise RuntimeError(
            "LITELLM_BASE_URL must be an absolute HTTP(S) gateway URL "
            "ending in /v1, for example http://localhost:4000/v1."
        )

    return ChatOpenAI(
        model="tripmate-primary",
        api_key=master_key,
        base_url=base_url,
        timeout=60,
        max_retries=0,
    )


llm = create_llm_client()


# =========================================================
# MCP client
# =========================================================

client = MultiServerMCPClient(
    {
        "tavily": {
            "transport": "streamable_http",
            "url": (
                "https://mcp.tavily.com/mcp/"
                f"?tavilyApiKey={TAVILY_API_KEY or ''}"
            ),
        },

        "aviationstack": {
            "transport": "stdio",
            "command": UVX_COMMAND,
            "args": [
                "aviationstack-mcp",
            ],
            "env": _subprocess_env(
                AVIATION_STACK_API_KEY=AVIATION_STACK_API_KEY,
            ),
        },

        "weather": {
            "transport": "stdio",

            # Uses the Python executable from the active Conda environment.
            "command": sys.executable,

            # Uses the weather server inside the current project folder.
            "args": [
                str(WEATHER_SERVER_PATH),
            ],

            "env": _subprocess_env(
                OPENWEATHER_API_KEY=OPENWEATHER_API_KEY,
            ),
        },
    }
)


# =========================================================
# MCP server tool loader
# =========================================================

async def _get_server_tool(
    server_name: str,
    tool_name: str,
):
    """
    Load one tool from one MCP server.

    This prevents a broken weather or AviationStack server from
    crashing an unrelated Tavily request.
    """

    if server_name == "tavily":
        _require_env(
            "TAVILY_API_KEY",
            TAVILY_API_KEY,
        )

    elif server_name == "aviationstack":
        _require_env(
            "AVIATION_STACK_API_KEY",
            AVIATION_STACK_API_KEY,
        )

        if shutil.which("uvx") is None:
            raise RuntimeError(
                "uvx was not found. Install uv, reopen the terminal, "
                "activate the travel environment, and run "
                "`uvx --version`."
            )

    elif server_name == "weather":
        _require_env(
            "OPENWEATHER_API_KEY",
            OPENWEATHER_API_KEY,
        )

        if not WEATHER_SERVER_PATH.is_file():
            raise FileNotFoundError(
                f"Weather MCP server not found: "
                f"{WEATHER_SERVER_PATH}"
            )

    # Important: load only the requested MCP server.
    tools = await client.get_tools(
        server_name=server_name,
    )

    tool = next(
        (
            item
            for item in tools
            if item.name == tool_name
        ),
        None,
    )

    if tool is None:
        available_tools = (
            ", ".join(
                sorted(item.name for item in tools)
            )
            or "none"
        )

        raise RuntimeError(
            f"MCP tool '{tool_name}' was not found "
            f"on server '{server_name}'. "
            f"Available tools: {available_tools}"
        )

    return tool


# =========================================================
# MCP connection test
# =========================================================

async def get_all_tools() -> None:
    """
    Test every MCP server independently.

    One failed server will not stop the remaining tests.
    """

    for server_name in (
        "tavily",
        "aviationstack",
        "weather",
    ):
        try:
            tools = await client.get_tools(
                server_name=server_name,
            )

            tool_names = (
                ", ".join(
                    tool.name
                    for tool in tools
                )
                or "no tools"
            )

            print(
                f"{server_name}: OK -> {tool_names}"
            )

        except Exception as exc:
            print(
                f"{server_name}: FAILED -> "
                f"{safe_error_message(exc)}"
            )


# =========================================================
# Tavily MCP
# =========================================================

async def tavily_mcp_search(query: str):
    search_tool = await _get_server_tool(
        "tavily",
        "tavily_search",
    )

    return await search_tool.ainvoke(
        {
            "query": query,
        }
    )


# =========================================================
# AviationStack MCP
# =========================================================

async def aviation_mcp_call(
    tool_name: str,
    tool_args: dict[str, Any] | None = None,
):
    aviation_tool = await _get_server_tool(
        "aviationstack",
        tool_name,
    )

    return await aviation_tool.ainvoke(
        tool_args or {}
    )


# =========================================================
# Weather MCP
# =========================================================

async def weather_mcp_search(city: str):
    weather_tool = await _get_server_tool(
        "weather",
        "get_current_weather",
    )

    return await weather_tool.ainvoke(
        {
            "city": city,
        }
    )


async def forecast_mcp_search(city: str):
    forecast_tool = await _get_server_tool(
        "weather",
        "get_forecast",
    )

    return await forecast_tool.ainvoke(
        {
            "city": city,
        }
    )


# =========================================================
# Destination extractor
# =========================================================

def _heuristic_destination(query: str) -> str:
    text = (query or "").strip()
    if not text:
        return ""

    import re

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
            if candidate and candidate.lower() not in {"i", "me"}:
                return candidate

    city_hints = [
        "paris",
        "tokyo",
        "dubai",
        "london",
        "delhi",
        "new york",
        "bangkok",
        "japan",
    ]
    lowered = text.lower()
    for city in city_hints:
        if city in lowered:
            return city.title()

    return ""


def extract_destination(
    query: str,
    invocation_counter: list[int] | None = None,
) -> str:
    heuristic = _heuristic_destination(query)
    if heuristic:
        return heuristic

    prompt = f"""
Extract only the destination city or country from the travel request.

Travel request:
{query}

Return only the destination name.
Do not add any explanation.
"""

    try:
        if invocation_counter is not None:
            invocation_counter[0] += 1
        response = llm.invoke(prompt)
        destination = str(response.content).strip()
    except Exception:
        raise ValueError("The destination could not be extracted.")

    if not destination:
        raise ValueError(
            "The destination could not be extracted."
        )

    return destination