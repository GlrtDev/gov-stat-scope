#!/usr/bin/env bash
set -euo pipefail

STACK_NAME="GovDataInfraStack"

echo "=> Resolving AWS resource identifiers from CloudFormation..."
BUCKET_NAME=${1:-$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --query "Stacks[0].Outputs[?OutputKey=='FrontendBucketName'].OutputValue" --output text)}
DOMAIN_NAME=$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDomainName'].OutputValue" --output text)

if [ -z "${BUCKET_NAME}" ] || [ "${BUCKET_NAME}" == "None" ]; then
    echo "Fatal: Could not resolve S3 Bucket Name from CloudFormation."
    exit 1
fi

DISTRIBUTION_ID=${2:-$(aws cloudfront list-distributions --query "DistributionList.Items[?DomainName=='${DOMAIN_NAME}'].Id" --output text)}

if [ -z "${DISTRIBUTION_ID}" ] || [ "${DISTRIBUTION_ID}" == "None" ]; then
    echo "Fatal: Could not resolve CloudFront Distribution ID from CloudFormation."
    exit 1
fi

echo "=> Building React frontend..."
cd "$(dirname "$0")/../frontend"
npm ci
npm run build

echo "=> Syncing static assets to S3 (s3://${BUCKET_NAME})..."
aws s3 sync dist/ "s3://${BUCKET_NAME}" --delete

echo "=> Invalidating CloudFront cache (${DISTRIBUTION_ID})..."
aws cloudfront create-invalidation --distribution-id "${DISTRIBUTION_ID}" --paths "/*"

echo "=> Frontend deployment complete. Live at: https://${DOMAIN_NAME}"