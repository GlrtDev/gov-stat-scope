#!/usr/bin/env bash
set -eo pipefail

TEST_TYPE="${1:-unit}"
TEST_PATH="${2}"

print_usage() {
    echo "Usage: ./scripts/test.sh [unit|integration|all|single <path>]"
    exit 1
}

# Navigate to repository root
cd "$(dirname "$0")/.."

cleanup() {
    echo "==> Cleaning up test containers..."
    docker compose stop dynamodb-local
}

echo "==> Building backend test container..."
docker compose build test-runner

case "$TEST_TYPE" in
    unit)
        echo "==> Executing unit tests..."
        docker compose run --rm test-runner pytest tests/unit -v --asyncio-mode=auto
        ;;
    integration)
        echo "==> Booting DynamoDB local dependency..."
        docker compose up -d dynamodb-local
        trap cleanup EXIT
        echo "==> Executing integration tests..."
        docker compose run --rm test-runner pytest tests/integration -v --asyncio-mode=auto
        ;;
    all)
        echo "==> Booting DynamoDB local dependency..."
        docker compose up -d dynamodb-local
        trap cleanup EXIT
        echo "==> Executing full test suite..."
        docker compose run --rm test-runner pytest tests -v --asyncio-mode=auto
        ;;
    single)
        if [ -z "$TEST_PATH" ]; then
            echo "Error: Must provide a test path."
            print_usage
        fi
        echo "==> Booting DynamoDB local dependency..."
        docker compose up -d dynamodb-local
        trap cleanup EXIT
        echo "==> Executing single test..."
        # Convert Windows backslashes to forward slashes for Linux container
        CLEAN_PATH="${TEST_PATH//\\//}"
        docker compose run --rm test-runner pytest "$CLEAN_PATH" -v --asyncio-mode=auto
        ;;
    *)
        print_usage
        ;;
esac