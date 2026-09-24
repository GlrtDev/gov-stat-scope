import os
from typing import Any

from aws_cdk import (
    BundlingOptions,
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
from urllib.parse import urlparse

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")


class GovDataInfraStack(Stack):
    """Minimal cost: Lambda + Function URL + CloudFront + DynamoDB + S3."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # 1. DynamoDB State Tables
        session_table = dynamodb.Table(
            self,
            "GovDataSessionsTable",
            table_name="govdata-sessions-v2",
            partition_key=dynamodb.Attribute(name="session_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="checkpoint_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.DESTROY,
        )

        quota_table = dynamodb.Table(
            self,
            "LLMQuotaTable",
            table_name="govdata-llm-quota",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
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
            # Web Adapter wraps uvicorn; run.sh starts the ASGI server
            handler="run.sh",
            layers=[
                lambda_.LayerVersion.from_layer_version_arn(
                    self,
                    "LambdaWebAdapterLayer",
                    f"arn:aws:lambda:{Stack.of(self).region}:753240598075:layer:LambdaAdapterLayerX86:30",
                )
            ],
            code=lambda_.Code.from_asset(
                BACKEND_DIR,
                bundling=BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                    command=[
                        "bash", "-c",
                        "mkdir -p /asset-output && "
                        "cp -r app /asset-output/ && "
                        "cp requirements-runtime.txt /asset-output/requirements.txt && "
                        "cp run.sh /asset-output/run.sh && "
                        "sed -i 's/\r$//' /asset-output/run.sh && "
                        "chmod +x /asset-output/run.sh && "
                        "pip install -r requirements-runtime.txt -t /asset-output"
                    ],
                ),
            ),
            memory_size=1024,
            timeout=Duration.minutes(5),
            environment={
                "DYNAMODB_TABLE_NAME": session_table.table_name,
                "DYNAMODB_QUOTA_TABLE": quota_table.table_name,
                "GUS_CACHE_TABLE": cache_table.table_name,
                "ENVIRONMENT": "production",
                "GUS_API_KEY": os.getenv("GUS_API_KEY", ""),
                "FRED_API_KEY": os.getenv("FRED_API_KEY", ""),
                "BEDROCK_ENABLED": os.getenv("BEDROCK_ENABLED", "false"),
                "BEDROCK_MODEL_ID": os.getenv("BEDROCK_MODEL_ID", "eu.amazon.nova-lite-v1:0"),
                "LLM_MODEL": os.getenv("BEDROCK_MODEL_ID", "eu.amazon.nova-lite-v1:0"),
                "AWS_LAMBDA_EXEC_WRAPPER": "/opt/bootstrap",
                "AWS_LWA_INVOKE_MODE": "response_stream",
            },
        )
        backend_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=[
                f"arn:aws:bedrock:*:{self.account}:inference-profile/eu.amazon.nova-lite-v1:0",
                "arn:aws:bedrock:*::foundation-model/amazon.nova-lite-v1:0",
            ],
        ))
        session_table.grant_read_write_data(backend_fn.role)
        cache_table.grant_read_write_data(backend_fn.role)
        quota_table.grant_read_write_data(backend_fn.role)

        # Function URL – RESPONSE_STREAM enables true SSE streaming
        fn_url = backend_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            invoke_mode=lambda_.InvokeMode.RESPONSE_STREAM,
            cors=lambda_.FunctionUrlCorsOptions(
                allowed_origins=["*"],
                allowed_methods=[lambda_.HttpMethod.ALL],
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
                    origin=origins.FunctionUrlOrigin(
                        fn_url,
                        read_timeout=Duration.seconds(60),
                    ),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                )
            },
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                ),
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                ),
            ],
        )

        CfnOutput(self, "DynamoDBTableName", value=session_table.table_name)
        CfnOutput(self, "FrontendBucketName", value=frontend_bucket.bucket_name)
        CfnOutput(self, "CloudFrontDomainName", value=distribution.distribution_domain_name)
        CfnOutput(self, "CloudFrontDistributionId", value=distribution.distribution_id)
        CfnOutput(self, "FunctionUrl", value=fn_url.url)