"""Command-line entry point.

A thin wrapper used by ``python -m coursellm`` and the ``coursellm`` console
script. It exists so operational commands have one discoverable home rather than
living in ad-hoc scripts at the repository root, which is how the previous
implementation accumulated stray ``check_*.py`` and ``fix_db.py`` files.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from coursellm import __version__
from coursellm.core.config import get_settings
from coursellm.core.logging import configure_logging


def _cmd_config(_: argparse.Namespace) -> int:
    """Print the effective configuration with secrets redacted."""
    from coursellm.core.logging import redact_event

    settings = get_settings()
    payload = settings.model_dump(mode="json")
    scrubbed = redact_event(None, "config", dict(payload))
    for key in sorted(scrubbed):
        print(f"{key} = {scrubbed[key]}")
    return 0


def _cmd_health(_: argparse.Namespace) -> int:
    """Print versions and configuration hashes; useful for support tickets."""
    from coursellm import __version__ as version

    settings = get_settings()
    print(f"coursellm {version}")
    print(f"environment={settings.environment.value}")
    print(f"retrieval_config_version={settings.retrieval_config_version}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coursellm", description="CourseLLM operations.")
    parser.add_argument("--version", action="version", version=f"coursellm {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("config", help="Print effective configuration (secrets redacted).").set_defaults(
        func=_cmd_config
    )
    sub.add_parser("health", help="Print version and configuration hashes.").set_defaults(
        func=_cmd_health
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging(get_settings())
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
