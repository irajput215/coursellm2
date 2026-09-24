"""Architecture fitness tests for the tenancy boundary.

These do not test behaviour at runtime; they test the *shape* of the codebase. A
boundary that holds today but can be removed by an ordinary commit is not a
control, it is a coincidence. Each test below fails the moment a specific way of
eroding the boundary is introduced.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from coursellm.db.base import GLOBAL_TABLES, TENANT_SCOPED_TABLES

pytestmark = pytest.mark.security

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "coursellm"
MIGRATIONS = pathlib.Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _python_files() -> list[pathlib.Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_names(path: pathlib.Path) -> set[str]:
    """Every name imported by a module, from any import form."""
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
            names.update(alias.asname for alias in node.names if alias.asname)
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
    return names


class TestModelCoverage:
    """A tenant-scoped table that is missing from the RLS list is unprotected."""

    def test_every_model_with_tenant_id_is_declared_tenant_scoped(self) -> None:
        import coursellm.db.models  # noqa: F401 - registers the metadata
        from coursellm.db.base import Base

        declared = {t.name for t in Base.metadata.tables.values() if "tenant_id" in t.columns}
        missing = declared - TENANT_SCOPED_TABLES

        assert not missing, (
            f"Tables carry tenant_id but are not protected by a Row-Level Security "
            f"policy: {sorted(missing)}. Add them to TENANT_SCOPED_TABLES and write a "
            f"migration that enables the policy."
        )

    def test_tenant_scoped_list_has_no_stale_entries(self) -> None:
        import coursellm.db.models  # noqa: F401
        from coursellm.db.base import Base

        declared = {t.name for t in Base.metadata.tables.values() if "tenant_id" in t.columns}
        stale = TENANT_SCOPED_TABLES - declared

        assert not stale, (
            f"TENANT_SCOPED_TABLES names tables that do not exist or no longer carry "
            f"tenant_id: {sorted(stale)}."
        )

    def test_global_tables_carry_no_tenant_id(self) -> None:
        """A 'global' table that acquired a tenant_id is a modelling error."""
        import coursellm.db.models  # noqa: F401
        from coursellm.db.base import Base

        for name in GLOBAL_TABLES:
            table = Base.metadata.tables.get(name)
            if table is None:
                continue
            assert "tenant_id" not in table.columns, (
                f"{name} is declared global but now carries tenant_id. Either give it "
                f"an RLS policy or remove the column."
            )


class TestMigrationMatchesCode:
    """The migration's table list is intentionally duplicated.

    Duplication is the point — a migration must describe the schema as it was at
    that revision, so it cannot import application code. That makes drift
    possible, so it is tested.
    """

    def _migration_rls_tables(self) -> set[str]:
        sources = [
            m.read_text()
            for m in MIGRATIONS.glob("*.py")
            if "TENANT_SCOPED_TABLES" in m.read_text()
        ]
        assert sources, "no migration declares the RLS table list"

        declared: set[str] = set()
        for source in sources:
            match = re.search(r"TENANT_SCOPED_TABLES\s*=\s*\((.*?)\)", source, re.DOTALL)
            assert match, "could not parse the migration's TENANT_SCOPED_TABLES"
            declared.update(re.findall(r'"([a-z_]+)"', match.group(1)))
        return declared

    def test_migration_protects_exactly_the_declared_tables(self) -> None:
        assert self._migration_rls_tables() == set(TENANT_SCOPED_TABLES), (
            "The tables protected by Row-Level Security in the migration differ from "
            "TENANT_SCOPED_TABLES. A table present in the code but absent from the "
            "migration has no policy and is unprotected."
        )


class TestAuthLookupIsContained:
    """``AuthLookup`` runs outside Row-Level Security by design.

    That makes it the most dangerous class in the codebase: any additional caller
    silently widens the set of code that can read across tenants. It is therefore
    restricted to the authentication service by test.
    """

    ALLOWED_CALLERS = {
        SRC / "services" / "auth.py",
        SRC / "repositories" / "identity.py",  # the definition itself
        SRC / "repositories" / "__init__.py",  # re-export
    }

    def test_only_the_auth_service_uses_the_cross_tenant_lookup(self) -> None:
        offenders = [
            str(path.relative_to(SRC))
            for path in _python_files()
            if path not in self.ALLOWED_CALLERS and "AuthLookup" in _imported_names(path)
        ]
        assert not offenders, (
            f"AuthLookup bypasses tenancy and must be reachable only from the "
            f"authentication service, but it is imported by: {offenders}."
        )


class TestTenantGucDiscipline:
    """The GUC must never be set with session scope."""

    def test_no_module_sets_the_tenant_variable_with_session_scope(self) -> None:
        """``SET app.tenant_id`` persists on a pooled connection.

        Only ``set_config(..., is_local => true)`` is safe. A session-scoped set
        would leak one request's tenant into the next request served by the same
        pooled connection.
        """
        pattern = re.compile(r"""SET\s+(LOCAL\s+)?app\.tenant_id""", re.IGNORECASE)
        offenders: list[str] = []

        for path in _python_files():
            text = path.read_text()
            for match in pattern.finditer(text):
                # ``SET LOCAL`` is transaction-scoped and therefore safe.
                if match.group(1) is None:
                    offenders.append(str(path.relative_to(SRC)))

        assert not offenders, (
            f"Session-scoped SET app.tenant_id found in {offenders}. Use "
            f"set_config('app.tenant_id', ..., true) so the value is reset when the "
            f"transaction ends."
        )

    def test_helpers_use_transaction_local_set_config(self) -> None:
        source = (SRC / "db" / "tenancy.py").read_text()
        assert "set_config(:name, :value, true)" in source, (
            "set_tenant_guc must use is_local => true; otherwise the value survives "
            "on a pooled connection and leaks across tenants."
        )


class TestRawSqlIsParameterised:
    """Any module issuing text() SQL must not interpolate values into it."""

    def test_no_f_string_sql_in_application_modules(self) -> None:
        offenders: list[str] = []
        pattern = re.compile(r"""text\(\s*f["']""")

        for path in _python_files():
            if "alembic" in path.parts:
                continue
            if pattern.search(path.read_text()):
                offenders.append(str(path.relative_to(SRC)))

        assert not offenders, (
            f"SQL built with an f-string found in {offenders}. Bind parameters "
            f"instead of interpolating them."
        )


class TestNoProviderSdkOutsideTheGateway:
    """Provider SDKs must not be imported by feature code.

    The whole point of routing every model call through the gateway is that
    routing, retries, fallback and cost accounting cannot be bypassed. An import
    of a provider SDK anywhere else is how that guarantee disappears.
    """

    FORBIDDEN = {"openai", "anthropic", "google", "groq", "mistralai", "cohere"}

    def test_feature_modules_do_not_import_provider_sdks(self) -> None:
        allowed_prefixes = ("llm/",)
        offenders: list[str] = []

        for path in _python_files():
            relative = str(path.relative_to(SRC))
            if relative.startswith(allowed_prefixes):
                continue
            imported = _imported_names(path)
            overlap = imported & self.FORBIDDEN
            if overlap:
                offenders.append(f"{relative} -> {sorted(overlap)}")

        assert not offenders, (
            f"Provider SDKs imported outside the LLM gateway: {offenders}. Route the "
            f"call through coursellm.llm instead."
        )
