#!/usr/bin/env python3
"""Launch the Netryx web API and bundled frontend."""

import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "netryx_web.api:app",
        host=os.environ.get("NETRYX_HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", os.environ.get("NETRYX_PORT", "8000"))),
        reload=False,
    )
