import os
from typing import Any

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_s3 as s3,
)
from constructs import Construct

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")


class GovDataInfraStack(Stack):
    """Minimal cost: Lambda + Function URL + CloudFront + DynamoDB + S3."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # 1. DynamoDB State Tables
        session_table = dynamodb.Table(
            self,
            "GovDataSessionsTable",
            table_name="govdata-sessions",
            partition_key=dynamodb.Attribute(name="session_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="expires_at",
            removal_policy=RemovalPolicy.DESTROY,
        )
        cache_table = dynamodb.Table(
            self,
            "GUSCacheTable",
            table_name="govstat-gus-cache",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # 2. Lambda backend (supports streaming)
        backend_fn = lambda_.Function(
            self,
            "GovDataBackendFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="app.handler",
            code=lambda_.Code.from_asset(BACKEND_DIR),
            memory_size=512,
            timeout=Duration.minutes(5),   # Your 3‑min limit
            environment={
                "DYNAMODB_TABLE_NAME": session_table.table_name,
                "GUS_CACHE_TABLE": cache_table.table_name,
                "ENVIRONMENT": "production",
            },
        )
        backend_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=["arn:aws:bedrock:*::foundation-model/anthropic.claude-3-haiku-20240307-v1:0"],
        ))
        backend_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=["arn:aws:secretsmanager:*:*:secret:govdata/*"],
        ))
        session_table.grant_read_write_data(backend_fn.role)
        cache_table.grant_read_write_data(backend_fn.role)

        # Function URL – enables streaming responses
        fn_url = backend_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,  # CloudFront will be the only public entry
            cors=lambda_.FunctionUrlCorsOptions(
                allowed_origins=["*"],  # tighten later via CloudFront
                allowed_methods=["*"],
                allowed_headers=["*"],
            ),
        )

        # 3. S3 bucket + CloudFront (static assets + API proxy)
        frontend_bucket = s3.Bucket(
            self,
            "GovDataFrontendBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        distribution = cloudfront.Distribution(
            self,
            "GovDataDistribution",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(frontend_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
            # API routes → Lambda Function URL (no buffering for streaming)
            additional_behaviors={
                "/api/*": cloudfront.BehaviorOptions(
                    origin=origins.HttpOrigin(fn_url.domain_name),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER,
                )
            },
            error_responses=[
                cloudfront.ErrorResponse(404, response_http_status=200, response_page_path="/index.html"),
                cloudfront.ErrorResponse(403, response_http_status=200, response_page_path="/index.html"),
            ],
        )

        CfnOutput(self, "DynamoDBTableName", value=session_table.table_name)
        CfnOutput(self, "FrontendBucketName", value=frontend_bucket.bucket_name)
        CfnOutput(self, "CloudFrontDomainName", value=distribution.distribution_domain_name)
        CfnOutput(self, "FunctionUrl", value=fn_url.url)