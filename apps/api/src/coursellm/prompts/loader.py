"""Prompt library: load, version, cache and render prompt files.

Prompts are files on disk (see ``prompts/README.md``), not string literals, so a
prompt change is a reviewable diff and a broken prompt fails at load rather than
in production. The library is deliberately synchronous and dependency-free: it
reads small files once and caches them, and it is safe to construct per request
because construction does no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from coursellm.core.config import Settings
from coursellm.core.errors import NotFoundError, ValidationError

# Front matter is ``---`` on its own line, a block of ``key: value`` lines, then
# a closing ``---`` line and the body. It is parsed by hand rather than with
# PyYAML because the schema is two scalar keys and the runtime dependency is not
# worth carrying for them.
_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?(.*)\Z", re.DOTALL)


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """One loaded, validated prompt file."""

    name: str
    version: int
    body: str
    path: Path

    @property
    def template_id(self) -> str:
        """The stable identifier recorded on every answer, e.g. ``tutor.answer@1``."""
        return f"{self.name}@{self.version}"


class _Placeholders(Mapping[str, str]):
    """A strict mapping for :meth:`str.format_map`.

    ``str.format`` raises a bare ``KeyError`` naming only the key, which is
    useless when a prompt is rendered from several call sites. Raising
    :class:`ValidationError` here propagates through ``format_map`` unchanged
    (it only intercepts ``KeyError``) and names the template, the missing key and
    the file to edit.
    """

    def __init__(self, template: PromptTemplate, values: Mapping[str, str]) -> None:
        self._template = template
        self._values = dict(values)

    def __getitem__(self, key: str) -> str:
        try:
            return self._values[key]
        except KeyError:
            available = ", ".join(sorted(self._values)) or "none"
            raise ValidationError(
                f"Prompt {self._template.template_id!r} requires the placeholder "
                f"{{{key}}}, which was not provided. Provided placeholders: {available}. "
                f"Template file: {self._template.path}."
            ) from None

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


class PromptLibrary:
    """Loads, validates and caches prompt templates from ``settings.resolved_prompts_dir``."""

    def __init__(self, settings: Settings) -> None:
        self._directory = settings.resolved_prompts_dir
        self._cache: dict[str, PromptTemplate] = {}

    @property
    def directory(self) -> Path:
        return self._directory

    def path_for(self, name: str) -> Path:
        """Map a dotted template name to its file (``tutor.answer`` -> ``tutor/answer.md``)."""
        return self._directory.joinpath(*name.split(".")).with_suffix(".md")

    def get(self, name: str) -> PromptTemplate:
        """Return the template, loading and caching it on first use.

        Raises :class:`~coursellm.core.errors.NotFoundError` naming the exact path
        that was looked for, so a missing prompt is a one-line fix rather than a
        hunt through the tree.
        """
        cached = self._cache.get(name)
        if cached is not None:
            return cached
        path = self.path_for(name)
        if not path.is_file():
            raise NotFoundError(f"Prompt {name!r} was not found. Looked for {path}.")
        template = parse_template(name, path, path.read_text(encoding="utf-8"))
        self._cache[name] = template
        return template

    def render(self, name: str, **kwargs: str) -> str:
        """Render ``name`` with ``kwargs`` using ``str.format_map``.

        Every referenced placeholder must be supplied; a missing one raises
        :class:`~coursellm.core.errors.ValidationError`. ``eval`` is never used.
        """
        template = self.get(name)
        return template.body.format_map(_Placeholders(template, kwargs))


def parse_template(name: str, path: Path, raw: str) -> PromptTemplate:
    """Parse and validate one prompt file's text.

    Validation is strict on purpose: a prompt with no front matter, a missing or
    non-positive ``version``, a mismatched ``name`` or an empty body is a load
    error, not something to paper over at render time.
    """
    match = _FRONT_MATTER_RE.match(raw)
    if match is None:
        raise ValidationError(
            f"Prompt file {path} has no front matter. Expected a block delimited by "
            f"'---' lines containing at least 'version' and 'name'."
        )

    fields = _parse_fields(match.group(1), path)
    declared = fields.get("name", name)
    if declared != name:
        raise ValidationError(
            f"Prompt file {path} declares name {declared!r} but was loaded as {name!r}; "
            "the front-matter name must match the file path."
        )

    raw_version = fields.get("version")
    if raw_version is None:
        raise ValidationError(f"Prompt file {path} has no 'version' in its front matter.")
    try:
        version = int(raw_version)
    except ValueError as exc:
        raise ValidationError(
            f"Prompt file {path} declares version {raw_version!r}, which is not an integer."
        ) from exc
    if version <= 0:
        raise ValidationError(
            f"Prompt file {path} declares version {version}; it must be positive."
        )

    body = match.group(2).strip()
    if not body:
        raise ValidationError(f"Prompt file {path} has an empty body.")

    return PromptTemplate(name=declared, version=version, body=body, path=path)


def _parse_fields(block: str, path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition(":")
        if not separator:
            raise ValidationError(
                f"Prompt file {path} has a malformed front-matter line: {line!r}."
            )
        fields[key.strip().lower()] = value.strip()
    return fields


__all__ = ["PromptLibrary", "PromptTemplate", "parse_template"]
