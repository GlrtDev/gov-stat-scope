import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.sessions import router as sessions_router
from app.storage.dynamodb_saver import init_dynamodb_tables

# Attempt to mount standard API endpoints if they exist
try:
    from app.routes import router as api_router
except ImportError:
    api_router = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Handles async startup events, ensuring infrastructure dependencies are initialized."""
    table_name = os.getenv("DYNAMODB_TABLE_NAME", "govdata-sessions")
    region_name = os.getenv("AWS_REGION", "us-east-1")
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