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


def _cmd_db_inspect(_: argparse.Namespace) -> int:
    """Report the database identity and whether RLS is actually enforced.

    Worth a dedicated command because "RLS is enabled" and "RLS is enforced for
    the role the application connects as" are different statements, and only the
    second one protects anything. A superuser, or any role holding BYPASSRLS,
    ignores every policy silently.
    """
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession

    from coursellm.db.session import get_engine, rls_enforcement_status

    async def _run() -> int:
        settings = get_settings()
        engine = await get_engine(settings)
        async with engine.connect() as conn:
            status = await rls_enforcement_status(AsyncSession(bind=conn))

        print(f"connected as     : {status['role']}")
        print(f"superuser        : {status['is_superuser']}")
        print(f"bypasses RLS     : {status['bypasses_rls']}")
        if status["enforced"]:
            print("tenant isolation : ENFORCED by Row-Level Security")
            return 0

        print("tenant isolation : NOT ENFORCED for this role")
        print(
            "  Superusers and roles with BYPASSRLS ignore Row-Level Security.\n"
            "  Repository-level scoping still applies, but the database backstop\n"
            "  does not. Run `make db-bootstrap` and connect as coursellm_app."
        )
        return 1

    return asyncio.run(_run())


def _cmd_seed_catalogue(_: argparse.Namespace) -> int:
    """Insert curated catalogue resources that are not already present.

    Idempotent on ``url`` and it never overwrites an existing row's metadata, so
    it is safe to run on every deploy. It is a CLI command rather than an Alembic
    data migration on purpose: a schema revision must not also be a data import,
    and catalogue review should be a separate, re-runnable step.
    """
    import asyncio

    from coursellm.db.session import get_session_factory
    from coursellm.recommend.seed import seed_catalogue

    async def _run() -> int:
        settings = get_settings()
        factory = await get_session_factory(settings)
        async with factory() as session, session.begin():
            report = await seed_catalogue(session)

        print(f"catalogue entries : {report.total}")
        print(f"inserted          : {report.inserted}")
        print(f"already present   : {report.skipped}")
        print(f"concept links new : {report.concept_links_inserted}")
        return 0

    return asyncio.run(_run())


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
    sub.add_parser(
        "db-inspect",
        help="Report the database role and whether Row-Level Security is enforced for it.",
    ).set_defaults(func=_cmd_db_inspect)
    sub.add_parser(
        "seed-catalogue",
        help="Insert missing curated catalogue resources (idempotent on URL).",
    ).set_defaults(func=_cmd_seed_catalogue)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging(get_settings())
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
