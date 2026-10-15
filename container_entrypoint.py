from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Sequence
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
LITELLM_HEALTH_URL = "http://127.0.0.1:4000/health/liveliness"
LITELLM_DATABASE_SETTINGS = {
    "STORE_MODEL_IN_DB",
    "DISABLE_SCHEMA_UPDATE",
    "DISABLE_PRISMA_HEALTH_CHECK_ON_STARTUP",
}


def validate_environment(environ: dict[str, str] | None = None) -> None:
    settings = os.environ if environ is None else environ
    missing = [
        name
        for name in ("LITELLM_MASTER_KEY", "GROQ_API_KEY", "GEMINI_API_KEY")
        if not settings.get(name, "").strip()
    ]
    if missing:
        raise RuntimeError(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + ". Set them in the Render service environment."
        )

    config_path = BASE_DIR / "litellm_config.yaml"
    if not config_path.is_file():
        raise RuntimeError(f"LiteLLM config file not found: {config_path.name}")


def gateway_command() -> list[str]:
    executable = shutil.which("litellm")
    if executable is None:
        raise RuntimeError(
            "LiteLLM executable was not installed; check the pinned "
            "litellm[proxy] dependency."
        )
    return [
        executable,
        "--config",
        str(BASE_DIR / "litellm_config.yaml"),
        "--host",
        "127.0.0.1",
        "--port",
        "4000",
    ]


def gateway_environment(
    environ: dict[str, str] | None = None,
) -> dict[str, str]:
    """Keep provider credentials but isolate LiteLLM from app database config."""

    settings = os.environ if environ is None else environ
    return {
        name: value
        for name, value in settings.items()
        if not (
            name.upper().startswith(("DATABASE_", "LITELLM_DATABASE_"))
            or name.upper() in LITELLM_DATABASE_SETTINGS
        )
    }


def application_command(environ: dict[str, str] | None = None) -> list[str]:
    settings = os.environ if environ is None else environ
    port = settings.get("PORT", "10000").strip()
    try:
        port_number = int(port)
    except ValueError as exc:
        raise RuntimeError("PORT must be an integer between 1 and 65535.") from exc
    if not 1 <= port_number <= 65535:
        raise RuntimeError("PORT must be an integer between 1 and 65535.")

    return [
        sys.executable,
        "-m",
        "uvicorn",
        "app:app",
        "--host",
        "0.0.0.0",
        "--port",
        str(port_number),
    ]


def http_service_ready(url: str, timeout: float = 2.0) -> bool:
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(url, timeout=timeout) as response:
            return response.status == 200
    except (OSError, TimeoutError, URLError):
        return False


def wait_for_service(
    name: str,
    url: str,
    process: subprocess.Popen[bytes],
    timeout: float = 60.0,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if should_stop is not None and should_stop():
            raise InterruptedError(f"Startup of {name} was interrupted.")
        exit_code = process.poll()
        if exit_code is not None:
            raise RuntimeError(
                f"{name} exited during startup with status {exit_code}."
            )
        if http_service_ready(url):
            return
        time.sleep(0.5)

    raise RuntimeError(f"{name} did not become healthy within {timeout:g} seconds.")


def _signal_process(process: subprocess.Popen[bytes], sig: int) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        process.terminate()
    else:
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass


def _stop_processes(processes: Sequence[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        _signal_process(process, signal.SIGTERM)

    for process in processes:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _signal_process(process, signal.SIGKILL)
            process.wait()


def main() -> int:
    load_dotenv(BASE_DIR / ".env")
    try:
        validate_environment()
        app_command = application_command()
        litellm_command = gateway_command()
    except RuntimeError as exc:
        print(f"Container configuration error: {exc}", file=sys.stderr, flush=True)
        return 1

    children: list[subprocess.Popen[bytes]] = []
    requested_signal: int | None = None

    def handle_signal(signum: int, _frame: object) -> None:
        nonlocal requested_signal
        requested_signal = signum

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        gateway = subprocess.Popen(
            litellm_command,
            cwd=BASE_DIR,
            env=gateway_environment(),
            start_new_session=os.name != "nt",
        )
        children.append(gateway)
        wait_for_service(
            "LiteLLM",
            LITELLM_HEALTH_URL,
            gateway,
            should_stop=lambda: requested_signal is not None,
        )

        app_port = app_command[-1]
        app = subprocess.Popen(
            app_command,
            cwd=BASE_DIR,
            start_new_session=os.name != "nt",
        )
        children.append(app)
        wait_for_service(
            "FastAPI",
            f"http://127.0.0.1:{app_port}/health",
            app,
            should_stop=lambda: requested_signal is not None,
        )
        print(
            "LiteLLM and FastAPI are healthy; container supervision started.",
            flush=True,
        )

        while requested_signal is None:
            for name, process in (("LiteLLM", gateway), ("FastAPI", app)):
                exit_code = process.poll()
                if exit_code is not None:
                    print(
                        f"{name} exited with status {exit_code}; stopping container.",
                        file=sys.stderr,
                        flush=True,
                    )
                    return exit_code or 1
            time.sleep(0.25)

        return 128 + requested_signal
    except InterruptedError:
        return 128 + (requested_signal or signal.SIGTERM)
    except (OSError, RuntimeError) as exc:
        print(f"Container startup failed: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        _stop_processes(children)


if __name__ == "__main__":
    raise SystemExit(main())
