# GovStatScope AI Orchestrator

GovStatScope is a stateful multi-agent orchestration platform built to query, normalize, and synthesize government statistical data. It leverages an autonomous AI workflow to route natural language queries to the appropriate statistical agency (GUS BDL for Poland, FRED for the US), construct accurate API requests, and perform comparative data analysis.

## Architecture & Tech Stack

- **Frontend**: React 18, TypeScript, Vite, TailwindCSS
- **Backend Core**: Python 3.11+, Async FastAPI, Pydantic v2
- **AI Orchestration**: LangGraph (Stateful Multi-Agent Workflow)
- **LLM Provider**: Amazon Bedrock (Claude 3 Haiku) enforcing strict JSON schemas
- **External Data Adapters**: Asynchronous clients for GUS (Bank Danych Lokalnych) and US FRED APIs
- **Infrastructure (AWS)**: 
  - ECS Fargate (Container Compute)
  - DynamoDB (Session State Checkpointing)
  - Secrets Manager (Secure API Key Storage)
  - S3 + CloudFront (Static Frontend Hosting)

## Local Development Setup

1. **Configure Environment Variables**:
   Copy the example environment file and populate it with your API keys.

```bash
cp .env.example .env
# Edit .env to include AWS credentials, GUS API key, and FRED API key
```

2. **Run via Docker Compose**:
Spins up the FastAPI backend, React frontend, and local DynamoDB instance.
```bash
docker compose up --build
```


3. **Access the Application**:
* Frontend UI: `http://localhost:3000`
* Backend API Docs: `http://localhost:8000/docs`


## 📋 Implementation Roadmap (TODO)

* [ ] S3 + CloudFront static frontend hosting & CI deploy
* [ ] ECS backend deployment (Task definitions, ALB, IAM roles)
* [ ] DynamoDB session state persistence & TTL
* [ ] CloudWatch logs & metrics dashboard
* [ ] Unit tests (GUS/FRED URL builders, normalization, math calculations)
* [ ] Integration tests (Mocked API fixtures, LangGraph flow, local DynamoDB)
* [ ] Pydantic contract validation for Bedrock JSON outputs
* [ ] Manual test scenarios (GUS Poland, US FRED, ambiguous & out-of-scope queries)
* [ ] Structured logging & Bedrock/DynamoDB cost tracking
* [ ] Architecture diagrams (Mermaid.js workflow)
* [ ] Demo script & interview talking points