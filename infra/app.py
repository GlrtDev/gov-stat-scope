#!/usr/bin/env python3
"""AWS CDK App entry point."""

import os
import aws_cdk as cdk
from stack import GovDataInfraStack

app = cdk.App()

GovDataInfraStack(
    app,
    "GovDataInfraStack",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    ),
)

app.synth()