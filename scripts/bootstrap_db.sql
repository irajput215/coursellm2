-- CourseLLM local role bootstrap.
--
-- Run once after creating the databases:
--
--     make db-create
--
-- ## Why two roles
--
-- The application must run as a role that CANNOT bypass Row-Level Security.
-- PostgreSQL ignores every policy for a superuser and for any role holding
-- BYPASSRLS, silently and without warning. Since a Homebrew or Docker PostgreSQL
-- install typically makes the local user a superuser, developing as that user
-- gives false confidence: the isolation tests pass under the superuser for the
-- wrong reason, and the failure appears only in production.
--
-- So local development mirrors production:
--
--   <owner>          owns the schema, runs migrations, may be a superuser
--   coursellm_app    runs the application and the tests; NOSUPERUSER NOBYPASSRLS
--
-- The application role is granted DML on tables and EXECUTE on the single
-- authentication lookup function, and nothing else. It cannot create, alter or
-- drop anything.
--
-- ## Passwords
--
-- No password is set here. Local connections use `trust` in the default
-- Homebrew and Docker `pg_hba.conf`, which is appropriate for a workstation and
-- is NOT appropriate anywhere else. In a deployed environment the role is
-- created with a password supplied from Secrets Manager, and the connection uses
-- TLS; see `infra/terraform/modules/rds` and `docs/architecture/deployment.md`.

\set ON_ERROR_STOP on

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'coursellm_app') THEN
        CREATE ROLE coursellm_app LOGIN
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOBYPASSRLS;
        RAISE NOTICE 'created role coursellm_app';
    ELSE
        -- Idempotent: re-running must not fail, and must not silently leave the
        -- role with elevated attributes from an earlier experiment.
        ALTER ROLE coursellm_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
        RAISE NOTICE 'role coursellm_app already exists; attributes reasserted';
    END IF;
END
$$;

-- The application needs to resolve unqualified table names.
GRANT USAGE ON SCHEMA public TO coursellm_app;

-- DML on everything that exists now...
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO coursellm_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO coursellm_app;

-- ...and on everything a future migration creates, so that adding a table does
-- not require remembering to re-run this script.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO coursellm_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO coursellm_app;

-- Tighten the authentication lookup. The default grants EXECUTE to PUBLIC, which
-- would let any role that can connect read password hashes. Revoking it and
-- granting only to the application role makes the function's audience explicit.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'auth_login_lookup') THEN
        REVOKE ALL ON FUNCTION auth_login_lookup(text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION auth_login_lookup(text) TO coursellm_app;
    END IF;
END
$$;
