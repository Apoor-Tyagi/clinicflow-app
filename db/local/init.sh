#!/bin/bash
# Runs once, the first time the local database volume is created (docker compose).
# Applies every migration as the database owner, then gives the application role a
# password so the API can sign in as a role that row-level security applies to.
set -e

for f in /migrations/*.sql; do
  echo "applying $f"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f "$f"
done

psql -v ON_ERROR_STOP=1 -v pw="$APP_DB_PASSWORD" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
ALTER ROLE clinicflow_app PASSWORD :'pw';
SQL
