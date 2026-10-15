from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from backend import run_travel_agent, resume_travel_agent
from container_entrypoint import LITELLM_HEALTH_URL
from mcp_client import safe_error_message


BASE_DIR = Path(__file__).resolve().parent


app = FastAPI(
    title="TripMate AI",
    description=(
        "LangGraph Multi-Agent Travel Planner with Supervisor, Guardrails, "
        "Human-in-the-Loop, and FastAPI Frontend"
    ),
    version="2.0.0",
)


app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)


templates = Jinja2Templates(
    directory=str(BASE_DIR / "templates")
)


class TravelRequest(BaseModel):
    message: str
    thread_id: str | None = None


class ApprovalRequest(BaseModel):
    thread_id: str = Field(min_length=1)
    approved: bool
    feedback: str = ""


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )


@app.post("/api/travel")
def travel_planner(request_data: TravelRequest):
    try:
        user_message = request_data.message.strip()

        if not user_message:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "Message cannot be empty.",
                },
            )

        result = run_travel_agent(
            user_input=user_message,
            thread_id=request_data.thread_id,
        )

        return JSONResponse(
            content={
                "success": True,
                **result,
            }
        )

    except Exception as exc:
        print(f"ERROR: {safe_error_message(exc)}")

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": safe_error_message(exc),
            },
        )


@app.post("/api/travel/approve")
def approve_travel_plan(request_data: ApprovalRequest):
    try:
        if (
            not request_data.approved
            and not request_data.feedback.strip()
        ):
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": (
                        "Please provide revision feedback "
                        "when rejecting the draft."
                    ),
                },
            )

        result = resume_travel_agent(
            thread_id=request_data.thread_id,
            approved=request_data.approved,
            feedback=request_data.feedback,
        )

        return JSONResponse(
            content={
                "success": True,
                **result,
            }
        )

    except Exception as exc:
        print(f"APPROVAL ERROR: {safe_error_message(exc)}")

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": safe_error_message(exc),
            },
        )


@app.get("/health")
async def health_check():
    try:
        with urlopen(LITELLM_HEALTH_URL, timeout=2) as response:
            gateway_healthy = response.status == 200
    except (OSError, TimeoutError, URLError):
        gateway_healthy = False

    content = {
        "status": "ok" if gateway_healthy else "degraded",
        "message": "TripMate AI API is running",
        "gateway": "ok" if gateway_healthy else "unavailable",
        "features": [
            "supervisor_agent",
            "input_guardrail",
            "human_in_the_loop",
        ],
    }
    return JSONResponse(
        status_code=200 if gateway_healthy else 503,
        content=content,
    )


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse(content={})


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )