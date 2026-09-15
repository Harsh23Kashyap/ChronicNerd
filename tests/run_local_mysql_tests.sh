#!/usr/bin/env bash
set -euo pipefail

command -v docker >/dev/null || {
  echo "Docker is required for the disposable MySQL test fixture." >&2
  exit 1
}
docker compose version >/dev/null || {
  echo "Docker Compose v2 is required (docker compose)." >&2
  exit 1
}

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
PORT=${CHRONICNERD_TEST_MYSQL_PORT:-33306}
PROJECT="chronicnerd-test-${USER:-local}-$$"
cleanup() { docker compose -p "$PROJECT" -f docker-compose.test.yml down -v --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

docker compose -p "$PROJECT" -f docker-compose.test.yml up -d --wait mysql-test

export host=127.0.0.1
export port="$PORT"
export user=root
export password=
export database=chronicnerd_test
export OPENAI_API_KEY=${OPENAI_API_KEY:-test-key-not-used-by-mocked-tests}
export RUN_MYSQL_INTEGRATION=1

python -m pytest -q tests/test_api_integration.py tests/test_mysql_concurrency.py
python -m pytest -q tests --ignore=tests/test_api_integration.py --ignore=tests/test_mysql_concurrency.py
