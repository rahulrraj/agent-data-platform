"""Thin CLI entrypoint over the composition root.

All wiring lives in `agent_api.composition` (the composition root); this
module only starts the server. Kept separate so the Lambda inbound adapter
can build the same app without importing a CLI module — and so the
composition root never imports a controller that imports it back.
"""

from __future__ import annotations

import uvicorn

from agent_api.composition import build_app
from agent_api.config.settings import Settings

__all__ = ["build_app", "main"]


def main() -> None:
    settings = Settings.from_env()
    app = build_app(settings)
    uvicorn.run(app, host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
