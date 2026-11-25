"""Local server entry point: python -m formaai."""

import argparse
import os

import uvicorn

from formaai.config import ConfigurationError, load_settings


def main() -> None:
    """Start the existing app factory without running database migrations."""
    parser = argparse.ArgumentParser(description="Run the FormaAI backend")
    parser.add_argument("--host", help="Override FORMAAI_HOST")
    parser.add_argument("--port", type=int, help="Override FORMAAI_PORT")
    parser.add_argument(
        "--reload",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override FORMAAI_RELOAD (development only)",
    )
    parser.add_argument(
        "--log-level",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="Override FORMAAI_LOG_LEVEL",
    )
    args = parser.parse_args()
    overrides = {key: value for key, value in vars(args).items() if value is not None}
    try:
        settings = load_settings(overrides)
    except ConfigurationError as exc:
        parser.error(str(exc))
    # The app factory and reload child must observe the same validated overrides.
    for key, value in overrides.items():
        os.environ[f"FORMAAI_{key.upper()}"] = (
            str(value).lower() if isinstance(value, bool) else str(value)
        )
    uvicorn.run(
        "formaai.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
