"""Versioned prompt files and the library that loads them.

Prompts live in ``prompts/`` at the repository root (or wherever
``PROMPTS_DIR`` points) and are loaded, validated and cached by
:class:`~coursellm.prompts.loader.PromptLibrary`. Keeping them out of Python
source means a prompt change is reviewable as a prompt change and a broken
prompt fails at load rather than mid-request.
"""

from __future__ import annotations

from coursellm.prompts.loader import PromptLibrary, PromptTemplate, parse_template

__all__ = ["PromptLibrary", "PromptTemplate", "parse_template"]
