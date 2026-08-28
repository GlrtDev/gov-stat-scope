# AWS Secrets Manager Rotation Guide

To automate the secure rotation of the `govdata/gus-api-key` and `govdata/fred-api-key` secrets, implement an AWS Lambda rotation function using the following 3 steps:

1. **Deploy the Custom Rotation Lambda**
   Create a Python AWS Lambda function using the `SecretsManagerRotationTemplate`. Implement the `create_secret` and `set_secret` methods to authenticate with the respective external vendor APIs (GUS BDL or US FRED) and generate a new access token.

2. **Grant IAM Permissions to the Lambda**
   Attach an execution role to your Lambda function granting it permission to call external internet endpoints, as well as `secretsmanager:PutSecretValue` and `secretsmanager:UpdateSecretVersionStage` on the specific `govdata/*` ARNs. Ensure the Secrets Manager service principal (`secretsmanager.amazonaws.com`) has `lambda:InvokeFunction` permissions.

3. **Attach the Rotation Schedule**
   Using the AWS CLI, bind the Lambda function to the secret and set a 30-day rotation window:
   ```bash
   aws secretsmanager rotate-secret \
       --secret-id govdata/gus-api-key \
       --rotation-lambda-arn arn:aws:lambda:<REGION>:<AWS_ACCOUNT_ID>:function:GovDataSecretRotation \
       --rotation-rules AutomaticallyAfterDays=30