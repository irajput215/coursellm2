"""Shared FastAPI dependencies.

Dependencies are the seam where tests substitute behaviour. Anything a router
needs — settings, a database session, a tenant context, the LLM gateway — is
obtained here rather than imported as a module-level singleton, so a test can
override it through ``app.dependency_overrides`` without patching modules.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from coursellm.core import config as config_module
from coursellm.core.config import Settings


def get_settings_dep() -> Settings:
    """Resolve application settings for a request.

    Goes through the module attribute rather than a bound import so that the
    cached accessor remains overridable in tests.
    """
    return config_module.get_settings()


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
