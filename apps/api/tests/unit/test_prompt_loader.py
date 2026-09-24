"""The prompt library: front matter, versioning and render-time validation.

The last two tests are the point of the suite: every prompt file on disk must
load, and every placeholder a body uses must be documented in
``prompts/README.md``. A prompt change that breaks either fails CI rather than
production.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from coursellm.core.config import Settings
from coursellm.core.errors import NotFoundError, ValidationError
from coursellm.prompts.loader import PromptLibrary, parse_template

pytestmark = pytest.mark.unit

_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _template_name(path: Path, root: Path) -> str:
    return ".".join(path.relative_to(root).with_suffix("").parts)


def _prompt_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.md") if path.name != "README.md")


def _write_prompt(directory: Path, name: str, text: str) -> Path:
    path = directory.joinpath(*name.split(".")).with_suffix(".md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_front_matter_is_parsed_and_template_id_is_name_at_version(settings: Settings) -> None:
    template = PromptLibrary(settings).get("tutor.answer")

    assert template.name == "tutor.answer"
    assert template.version == 1
    assert template.template_id == "tutor.answer@1"
    assert "untrusted_evidence" in template.body
    assert template.path.name == "answer.md"


def test_missing_prompt_raises_not_found_naming_the_path(settings: Settings) -> None:
    library = PromptLibrary(settings)

    with pytest.raises(NotFoundError) as excinfo:
        library.get("tutor.does_not_exist")

    assert str(library.path_for("tutor.does_not_exist")) in str(excinfo.value)


def test_missing_placeholder_raises_validation_error_naming_the_key(settings: Settings) -> None:
    library = PromptLibrary(settings)

    with pytest.raises(ValidationError) as excinfo:
        library.render("tutor.answer", course_name="Algorithms")

    message = str(excinfo.value)
    assert "evidence" in message
    assert "tutor.answer@1" in message


def test_render_substitutes_provided_placeholders(settings: Settings) -> None:
    rendered = PromptLibrary(settings).render(
        "tutor.answer", course_name="Algorithms", evidence="[S1] A passage."
    )

    assert "Algorithms" in rendered
    assert "[S1] A passage." in rendered
    assert "{course_name}" not in rendered
    assert "{evidence}" not in rendered


def test_every_prompt_file_on_disk_loads(settings: Settings) -> None:
    root = settings.resolved_prompts_dir
    library = PromptLibrary(settings)
    files = _prompt_files(root)

    assert files, f"no prompt files found under {root}"
    for path in files:
        template = library.get(_template_name(path, root))
        assert template.body
        assert template.version >= 1
        assert template.path == path


def test_every_placeholder_is_documented_in_the_readme(settings: Settings) -> None:
    root = settings.resolved_prompts_dir
    readme = (root / "README.md").read_text(encoding="utf-8")
    library = PromptLibrary(settings)

    documented: set[str] = set()
    for path in _prompt_files(root):
        template = library.get(_template_name(path, root))
        for placeholder in _PLACEHOLDER_RE.findall(template.body):
            documented.add(placeholder)
            assert f"{{{placeholder}}}" in readme, (
                f"{template.path} uses {{{placeholder}}} but prompts/README.md does not "
                "document it in the placeholder registry"
            )

    assert documented == {"course_name", "evidence", "question", "excerpts"}


def test_front_matter_is_required(settings: Settings, tmp_path: Path) -> None:
    directory = tmp_path / "prompts"
    directory.mkdir()
    _write_prompt(directory, "tutor.answer", "No front matter here.\n")
    scoped = settings.model_copy(update={"prompts_dir": str(directory)})

    with pytest.raises(ValidationError, match="front matter"):
        PromptLibrary(scoped).get("tutor.answer")


def test_version_must_be_a_positive_integer(settings: Settings, tmp_path: Path) -> None:
    directory = tmp_path / "prompts"
    directory.mkdir()
    _write_prompt(directory, "tutor.answer", "---\nversion: 0\nname: tutor.answer\n---\nBody\n")
    scoped = settings.model_copy(update={"prompts_dir": str(directory)})

    with pytest.raises(ValidationError, match="positive"):
        PromptLibrary(scoped).get("tutor.answer")


def test_body_must_not_be_empty(settings: Settings, tmp_path: Path) -> None:
    directory = tmp_path / "prompts"
    directory.mkdir()
    _write_prompt(directory, "tutor.answer", "---\nversion: 1\nname: tutor.answer\n---\n   \n")
    scoped = settings.model_copy(update={"prompts_dir": str(directory)})

    with pytest.raises(ValidationError, match="empty body"):
        PromptLibrary(scoped).get("tutor.answer")


def test_front_matter_name_must_match_the_file_path(tmp_path: Path) -> None:
    path = _write_prompt(
        tmp_path, "tutor.answer", "---\nversion: 1\nname: tutor.wrong\n---\nBody\n"
    )

    with pytest.raises(ValidationError, match="declares name"):
        parse_template("tutor.answer", path, path.read_text(encoding="utf-8"))
