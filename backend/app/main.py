import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.sessions import router as sessions_router
from app.services.secrets_client import AsyncSecretsClient
from app.storage.dynamodb_saver import init_dynamodb_tables

logger = logging.getLogger(__name__)

# Attempt to mount standard API endpoints if they exist
try:
    from app.routes import router as api_router
except ImportError:
    api_router = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Handles async startup events, ensuring infrastructure dependencies are initialized."""
    region_name = os.getenv("AWS_REGION", "us-east-1")
    
    # 1. Load Secrets into environment if running in production AWS ECS
    if os.getenv("ENVIRONMENT") == "production":
        logger.info("Production environment detected. Fetching secrets from AWS Secrets Manager.")
        secrets_client = AsyncSecretsClient(region_name=region_name)
        
        try:
            gus_secret = await secrets_client.get_secret("govdata/gus-api-key")
            fred_secret = await secrets_client.get_secret("govdata/fred-api-key")
            
            # Inject secrets directly into os.environ to act as local environment variables
            for key, value in gus_secret.items():
                os.environ[key] = str(value)
            for key, value in fred_secret.items():
                os.environ[key] = str(value)
                
            logger.info("Successfully injected production API keys from Secrets Manager.")
        except Exception as e:
            logger.critical(f"Failed to load required secrets from AWS. Service may degrade: {e}")
            raise

    # 2. Initialize DynamoDB Checkpointer Table
    table_name = os.getenv("DYNAMODB_TABLE_NAME", "govdata-sessions")
    await init_dynamodb_tables(table_name=table_name, region_name=region_name)
    
    yield


app = FastAPI(
    title="GovData AI Orchestrator",
    description="Stateful multi-agent orchestration API for government data sources.",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if api_router:
    app.include_router(api_router)
    
app.include_router(sessions_router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}