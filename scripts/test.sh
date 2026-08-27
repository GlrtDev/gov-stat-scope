#!/usr/bin/env bash
set -eo pipefail

TEST_TYPE="${1:-unit}"

print_usage() {
    echo "Usage: ./scripts/test.sh [unit|integration|all]"
    exit 1
}

# Navigate to repository root
cd "$(dirname "$0")/.."

echo "==> Building backend test container..."
docker compose build test-runner

case "$TEST_TYPE" in
    unit)
        echo "==> Executing unit tests..."
        docker compose run --rm test-runner tests/unit -v
        ;;
    integration)
        echo "==> Booting DynamoDB local dependency..."
        docker compose up -d dynamodb-local
        echo "==> Executing integration tests..."
        docker compose run --rm test-runner tests/integration -v
        echo "==> Cleaning up test containers..."
        docker compose stop dynamodb-local
        ;;
    all)
        echo "==> Booting DynamoDB local dependency..."
        docker compose up -d dynamodb-local
        echo "==> Executing full test suite..."
        docker compose run --rm test-runner tests -v
        echo "==> Cleaning up test containers..."
        docker compose stop dynamodb-local
        ;;
    *)
        print_usage
        ;;
esac