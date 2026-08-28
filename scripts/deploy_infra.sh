#!/usr/bin/env bash
set -euo pipefail

echo "=> Initializing CDK environment..."
cd "$(dirname "$0")/../infra"

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "=> Bootstrapping AWS environment..."
cdk bootstrap

echo "=> Deploying GovDataInfraStack..."
cdk deploy --require-approval never

echo "=> Infrastructure deployed successfully."