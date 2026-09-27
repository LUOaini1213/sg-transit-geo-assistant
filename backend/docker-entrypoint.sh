#!/bin/sh
# Serve the database at $TRANSIT_DB (mounted from the host). Without one, build and serve the small test fixture,
# so the stack still starts in CI or on a machine that has not prepared the full data.
set -e
if [ ! -f "${TRANSIT_DB:-/data/transit.duckdb}" ]; then
  echo "No database at ${TRANSIT_DB:-/data/transit.duckdb}; serving the test fixture instead."
  python -m backend.app.build data/fixture /tmp/fixture.duckdb
  export TRANSIT_DB=/tmp/fixture.duckdb
fi
exec uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
