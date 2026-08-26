# GovData AI Orchestrator

## Tech Stack
*   **Backend:** Python 3.12+, FastAPI, LangGraph, Pydantic, boto3
*   **Frontend:** React, TypeScript, Vite
*   **Infrastructure:** AWS (ECS/Fargate, S3, CloudFront), Docker Compose

## Build & Test Commands
*   **Run Local:** `docker compose up -d`
*   **Test:** `pytest backend/tests/`
*   **Lint:** `ruff check . --fix`
*   **Type Check:** `mypy backend/`
*   **UI Dev:** `npm run dev` (inside `/frontend`)

## Architectural Constraints
*   **State Management:** `OrchestratorState` is immutable outside of explicit LangGraph node transitions.
*   **Data Normalization:** Enforce strict Pydantic validation on all GUS and FRED adapter responses before passing data to the Data Analyst agent.
*   **Security:** Never hardcode API keys. Rely exclusively on environment variables and `boto3` Secrets Manager calls.
*   **LLM Guardrails:** Bedrock Claude Haiku invocations must use strict JSON-mode prompts and tool-calling schemas.

## Agent Instructions
*   Output code directly. Skip conversational filler, apologies, and lengthy explanations.
*   Keep functions modular. Prefer early returns and explicit error handling for third-party HTTP requests.
*   Do not hallucinate architectural changes outside the specified roadmap in `docs/roadmap.md`.