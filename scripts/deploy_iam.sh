#!/bin/bash
set -e

# Retrieve dynamic AWS Account ID and Region
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=${AWS_REGION:-us-east-1}

echo "Deploying IAM roles for Account: $ACCOUNT_ID in Region: $REGION"

# 1. Create ECS Task Role (Application Permissions)
aws iam create-role \
    --role-name GovDataECSTaskRole \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

sed -e "s/<AWS_ACCOUNT_ID>/$ACCOUNT_ID/g" -e "s/<REGION>/$REGION/g" infra/iam/ecs_task_role_policy.json > /tmp/ecs_task_role_policy_hydrated.json

aws iam put-role-policy \
    --role-name GovDataECSTaskRole \
    --policy-name GovDataTaskPolicy \
    --policy-document file:///tmp/ecs_task_role_policy_hydrated.json

# 2. Create ECS Execution Role (Infrastructure Permissions)
aws iam create-role \
    --role-name GovDataECSExecutionRole \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

sed -e "s/<AWS_ACCOUNT_ID>/$ACCOUNT_ID/g" -e "s/<REGION>/$REGION/g" infra/iam/ecs_execution_role_policy.json > /tmp/ecs_execution_role_policy_hydrated.json

aws iam put-role-policy \
    --role-name GovDataECSExecutionRole \
    --policy-name GovDataExecutionPolicy \
    --policy-document file:///tmp/ecs_execution_role_policy_hydrated.json

# Cleanup
rm /tmp/ecs_task_role_policy_hydrated.json /tmp/ecs_execution_role_policy_hydrated.json

echo "IAM deployment complete."