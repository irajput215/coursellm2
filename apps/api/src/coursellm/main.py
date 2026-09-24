"""ASGI entry point.

Run with::

    uvicorn coursellm.main:app --reload

or through the Makefile::

    make api
"""

from __future__ import annotations

from coursellm.api.app import create_app

app = create_app()

__all__ = ["app"]
