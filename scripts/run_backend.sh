#!/usr/bin/env bash
set -e

echo "Starting DynamoDB Local and GovStatScope Backend..."
docker compose up --build backend dynamodb-local