"""AWS CDK stack for GovStatScope infrastructure."""

from aws_cdk import (
    CfnOutput,
    RemovalPolicy,
    Stack,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_dynamodb as dynamodb,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_logs as logs,
    aws_s3 as s3,
)
from constructs import Construct


class GovDataInfraStack(Stack):
    """Provisions DynamoDB, CloudFront, Fargate Spot ECS, and ALB."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs: dict[str, str]) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # 1. DynamoDB State Table
        session_table = dynamodb.Table(
            self,
            "GovDataSessionsTable",
            table_name="govdata-sessions",
            partition_key=dynamodb.Attribute(
                name="session_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="expires_at",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # 2. S3 Bucket & CloudFront for Frontend Assets
        frontend_bucket = s3.Bucket(
            self,
            "GovDataFrontendBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        distribution = cloudfront.Distribution(
            self,
            "GovDataFrontendDistribution",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(frontend_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
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
                )
            ]
        )

        # 3. Networking (Cost-optimized: 2 AZs, 0 NAT Gateways, Public Subnets)
        vpc = ec2.Vpc(
            self,
            "GovDataVpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                )
            ],
        )

        # 4. ECS Cluster & CloudWatch Logs
        cluster = ecs.Cluster(self, "GovDataCluster", vpc=vpc, enable_fargate_capacity_providers=True)

        log_group = logs.LogGroup(
            self,
            "GovDataLogGroup",
            log_group_name="/ecs/govdata-backend",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # 5. IAM Roles for Fargate Task
        task_role = iam.Role(
            self, "GovDataTaskRole", assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com")
        )
        task_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=["arn:aws:bedrock:*::foundation-model/anthropic.claude-3-haiku-20240307-v1:0"],
        ))
        task_role.add_to_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=["arn:aws:secretsmanager:*:*:secret:govdata/*"],
        ))
        session_table.grant_read_write_data(task_role)

        # 6. Fargate Task Definition
        task_definition = ecs.FargateTaskDefinition(
            self,
            "GovDataTaskDef",
            cpu=256,
            memory_limit_mib=512,
            task_role=task_role,
        )

        container = task_definition.add_container(
            "GovDataContainer",
            image=ecs.ContainerImage.from_registry("public.ecr.aws/docker/library/nginx:latest"),  # Placeholder ECR image
            logging=ecs.LogDriver.aws_logs(
                stream_prefix="backend",
                log_group=log_group,
            ),
            environment={
                "DYNAMODB_TABLE_NAME": session_table.table_name,
                "ENVIRONMENT": "production",
            }
        )
        container.add_port_mappings(ecs.PortMapping(container_port=8000))

        # 7. ECS Service (Fargate Spot in Public Subnets)
        service = ecs.FargateService(
            self,
            "GovDataService",
            cluster=cluster,
            task_definition=task_definition,
            assign_public_ip=True,  # Required to pull ECR images without NAT Gateway
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            capacity_provider_strategies=[
                ecs.CapacityProviderStrategy(capacity_provider="FARGATE_SPOT", weight=1)
            ],
        )

        # 8. Application Load Balancer
        alb = elbv2.ApplicationLoadBalancer(
            self,
            "GovDataALB",
            vpc=vpc,
            internet_facing=True,
        )
        listener = alb.add_listener("HttpListener", port=80)
        listener.add_targets(
            "GovDataTargetGroup",
            port=80,
            targets=[service.load_balancer_target(
                container_name="GovDataContainer",
                container_port=8000
            )],
            health_check=elbv2.HealthCheck(
                path="/health/live",
                interval=cdk.Duration.seconds(30),
            ),
        )

        # Outputs
        CfnOutput(self, "DynamoDBTableName", value=session_table.table_name)
        CfnOutput(self, "FrontendBucketName", value=frontend_bucket.bucket_name)
        CfnOutput(self, "CloudFrontDomainName", value=distribution.distribution_domain_name)
        CfnOutput(self, "ApiLoadBalancerDns", value=alb.load_balancer_dns_name)