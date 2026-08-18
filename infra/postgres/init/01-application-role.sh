#!/bin/bash
# The unprivileged role the application connects as.
#
# Every table in this schema is FORCE ROW LEVEL SECURITY, so the owner is bound
# by the policies too — but the separation still matters. The owner can drop
# tables, disable policies, and create the auth lookup function; the application
# can do none of those. Running the app as the owner locally would mean the
# first time that difference is exercised is in production.
#
# A shell script rather than plain SQL so the password comes from the same
# environment variable docker-compose builds the DSN from. A hardcoded password
# here would silently diverge the moment anyone sets RFP_APP_PASSWORD.
#
# Runs once, on an empty data directory: Postgres only executes this directory
# on first init, so re-running against an existing volume is a no-op.
set -euo pipefail

APP_PASSWORD="${RFP_APP_PASSWORD:-rfp_app}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE rfp_app LOGIN PASSWORD '${APP_PASSWORD}';

GRANT USAGE ON SCHEMA public TO rfp_app;

-- Applies to tables created *after* this statement — which is every table,
-- since migrations run later. Without it each new migration would silently
-- create a table the application cannot read.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO rfp_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO rfp_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO rfp_app;

-- Deliberately absent: BYPASSRLS. Cross-tenant work needs a separate,
-- explicitly privileged role, so no accidental query can see another tenant's
-- rows.
SQL
