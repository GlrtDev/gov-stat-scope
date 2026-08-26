# GovData AI Orchestrator Coding Work Plan

## Phase 0 - Repository and Local Baseline
Estimate: 1 day

- Create monorepo structure:
  - frontend/
  - backend/
  - infra/
  - docs/
  - scripts/
- Initialize Python project with FastAPI, LangGraph, Pydantic, httpx, boto3.
- Initialize React + TypeScript Vite app for chat UI.
- Add Dockerfile for backend service.
- Add docker-compose.yml for local development:
  - FastAPI backend
  - DynamoDB Local
  - optional mock Bedrock endpoint or stub LLM client
- Add .env.example with required variables:
  - GUS_API_KEY
  - FRED_API_KEY
  - AWS_REGION
  - DYNAMODB_TABLE_NAME
  - BEDROCK_MODEL_ID
- Add linting, formatting, type checking, and tests:
  - ruff
  - mypy
  - pytest
  - GitHub Actions CI

Deliverable:
- Local backend starts with docker compose.
- Frontend can call /health and /ask endpoints locally.
- CI runs lint, type checks, and unit tests.

## Phase 1 - Core Domain Models and API Contract
Estimate: 1 day

- Define Pydantic models:
  - ChatMessage
  - UserQuery
  - DataSource enum: GUS, FRED
  - ApiRequestPlan
  - RawApiResponse
  - AnalyzedResult
  - OrchestratorState
- Define FastAPI endpoints:
  - GET /health
  - POST /ask
  - optional GET /ask/stream for SSE responses
- Define request/response schemas:
  - AskRequest { message, session_id }
  - AskResponse { answer, source, metadata }
- Add basic error handling middleware.
- Add structured logging with request IDs and trace IDs.

Deliverable:
- Stable API contract between frontend, FastAPI, and LangGraph workflow.

## Phase 2 - Government Data Adapters
Estimate: 3 days

### GUS BDL Adapter
- Create GUSClient class.
- Implement authentication using X-ClientId header from Secrets Manager or environment variable.
- Build URL construction helpers for Bank Danych Lokalnych endpoints.
- Add metadata discovery support:
  - list regions
  - list variables
  - resolve variable IDs by name or description
- Implement request methods:
  - fetch_series()
  - fetch_comparison()
  - fetch_time_range()
- Normalize GUS JSON into a common internal schema:
  - source = "GUS"
  - metric_name
  - region
  - time_period
  - values[]
- Add retry logic with exponential backoff.
- Add timeout handling and clear error messages for invalid IDs or missing data.

### FRED Adapter
- Create FredClient class.
- Implement API key authentication via query parameter or header as required by FRED.
- Build series resolution helpers:
  - map natural language metric names to FRED series IDs
  - support common metrics such as CPI, unemployment, GDP, interest rates
- Implement request methods:
  - fetch_series()
  - fetch_comparison()
  - fetch_time_range()
- Normalize FRED JSON into the same internal schema used for GUS.
- Add retry logic, timeout handling, and error normalization.

### Shared Adapter Interface
- Define DataSourceClient abstract base class.
- Implement:
  - resolve_query()
  - fetch_data()
  - normalize_response()
- Create a registry mapping DataSource enum values to client instances.

Deliverable:
- Backend can fetch real data from GUS and FRED using secure API keys.
- Both sources return normalized time-series objects usable by the analyst agent.

## Phase 3 - LangGraph Multi-Agent Workflow
Estimate: 4 days

### State Design
- Define OrchestratorState with fields:
  - session_id
  - user_query
  - messages[]
  - selected_source
  - api_plan
  - raw_response
  - normalized_data
  - analysis_result
  - final_answer
  - errors[]
  - metadata
- Add validation for state transitions.

### Node 1: Intent and Routing Agent
- Create router agent node.
- Use Bedrock Claude Haiku to classify the query:
  - Polish regional/economic data -> GUS
  - US macroeconomic data -> FRED
  - unsupported source -> clarification response
- Output structured JSON containing:
  - selected_source
  - confidence
  - extracted_entities
  - reason
- Add deterministic fallback rules:
  - if query contains Poland, GUS, region names, or Polish metric terms, prefer GUS.
  - if query contains US, United States, FRED, CPI, GDP, unemployment rate in US context, prefer FRED.
- Route to the correct API engineer subgraph.

### Node 2: API Engineer Agent
- Create tool-calling agent node.
- Provide tools:
  - list_gus_regions()
  - search_gus_variables()
  - build_gus_request()
  - fetch_gus_data()
  - resolve_fred_series()
  - fetch_fred_data()
- Agent responsibilities:
  - extract metric, region/country, time range, comparison target
  - construct exact API request parameters
  - call the appropriate data adapter
  - return normalized data or a structured error
- Enforce JSON-only tool arguments.
- Add guardrails to prevent invalid URL construction.

### Node 3: Data Analyst Agent
- Create analyst agent node.
- Input:
  - user query
  - normalized GUS or FRED data
- Responsibilities:
  - calculate changes, averages, differences, percentages
  - compare regional vs national values when requested
  - summarize trends in natural language
  - cite source and time range
- Output structured result containing:
  - final_answer
  - key_metrics[]
  - calculations_performed[]
  - data_source
  - confidence_notes

### Graph Edges
- START -> router_agent
- router_agent -> api_engineer_agent if supported source selected
- router_agent -> END with clarification if unsupported or ambiguous
- api_engineer_agent -> analyst_agent on successful fetch
- api_engineer_agent -> error_handler_node on failure
- analyst_agent -> END
- error_handler_node -> END

Deliverable:
- LangGraph workflow can route a query, call the correct government API, analyze data, and return an answer.

## Phase 4 - Amazon Bedrock Integration
Estimate: 2 days

- Create BedrockClient wrapper using boto3.
- Implement invoke_model with Claude 3 Haiku.
- Add JSON response enforcement:
  - system prompt requiring strict JSON output
  - Pydantic validation of model output
  - retry on invalid JSON
- Implement tool calling support for the API Engineer agent.
- Add token usage tracking and cost logging.
- Add fallback behavior if Bedrock is unavailable:
  - deterministic router only
  - return clear error to frontend

Deliverable:
- Production-ready Bedrock integration with validated structured outputs and observability.

## Phase 5 - DynamoDB Memory and Checkpointing
Estimate: 2 days

- Create DynamoDB table for conversation state:
  - partition key: session_id
  - sort key: step_id or timestamp
  - TTL field for automatic cleanup
- Implement LangGraph checkpointer using DynamoDB.
- Store:
  - message history
  - selected source
  - API plan
  - normalized data summary
  - final answer
  - execution metadata
- Add session retrieval endpoint if needed:
  - GET /sessions/{session_id}
- Add cleanup job or TTL policy for old sessions.

Deliverable:
- Multi-turn conversations persist across requests.
- LangGraph can resume state from DynamoDB.

## Phase 6 - Secrets Manager and Security
Estimate: 2 days

- Create AWS Secrets Manager secrets:
  - gus-api-key
  - fred-api-key
- Update backend to load secrets at startup or per request using boto3.
- Remove all hardcoded API keys from code, Docker image, and environment examples.
- Define IAM roles:
  - ECS task role with permission to read Secrets Manager secrets
  - Bedrock model invocation permission
  - DynamoDB read/write permission for the state table
  - CloudWatch Logs write permission
- Add least-privilege policy JSON files under infra/.
- Add secret rotation notes or optional Lambda rotation function.

Deliverable:
- API keys are never stored in source control or container images.
- Backend can securely retrieve secrets from AWS Secrets Manager.

## Phase 7 - FastAPI Production Hardening
Estimate: 2 days

- Add request validation with Pydantic.
- Add rate limiting per session or IP.
- Add CORS configuration for frontend origin.
- Add structured JSON logging.
- Add OpenAPI documentation.
- Add health checks:
  - /health returns service status
  - optional readiness check for DynamoDB and Bedrock connectivity
- Add graceful shutdown handling.
- Add request timeout controls for external API calls.

Deliverable:
- Backend is production-ready for containerized deployment.

## Phase 8 - React Frontend
Estimate: 3 days

- Build chat interface with TypeScript.
- Implement message list and input form.
- Call POST /ask from frontend.
- Display:
  - final answer
  - data source badge
  - metadata such as time range or metric names
- Add loading, error, and retry states.
- Optional streaming support:
  - consume SSE from /ask/stream
  - render partial analyst response progressively
- Build production static bundle with Vite.

Deliverable:
- Static React app can query the orchestrator and display synthesized answers.

## Phase 9 - AWS Infrastructure as Code
Estimate: 4 days

### S3 + CloudFront Frontend Hosting
- Create S3 bucket for frontend static assets.
- Enable versioning or lifecycle rules if needed.
- Create CloudFront distribution pointing to S3 origin.
- Configure HTTPS, caching headers, and optional custom domain.
- Add CI step to upload React build artifacts to S3.

### Backend Deployment Option A: ECS on EC2 t3.micro
- Launch t3.micro EC2 instance in a private or public subnet as appropriate.
- Install Docker Engine and configure ECS agent.
- Create ECS cluster.
- Create task definition for FastAPI container.
- Configure ALB or direct HTTPS endpoint if using CloudFront origin.
- Add IAM role to EC2/ECS service.

### Backend Deployment Option B: Fargate Spot
- Create ECS Fargate service with Spot capacity.
- Define task definition with CPU/memory limits.
- Configure load balancer target group.
- Add auto-scaling or minimum/maximum desired count settings.
- Keep cost low by using small task size and Spot pricing.

### DynamoDB
- Create DynamoDB table for session state.
- Enable on-demand capacity or minimal provisioned capacity.
- Set TTL attribute for automatic cleanup.

### CloudWatch
- Configure log groups:
  - /ecs/govdata-backend
  - /aws/cloudfront/frontend if needed
- Add basic alarms for errors and latency.

Deliverable:
- Infrastructure can be deployed with minimal manual steps.
- Frontend is served from S3 + CloudFront.
- Backend runs as a container on low-cost AWS compute.

## Phase 10 - Testing Strategy
Estimate: 3 days

### Unit Tests
- Test GUS URL builder and response normalization.
- Test FRED series resolution and response normalization.
- Test router classification with deterministic fixtures.
- Test analyst calculations such as percentage change and average comparison.

### Integration Tests
- Use recorded API responses for GUS and FRED to avoid live calls in CI.
- Test LangGraph end-to-end flow:
  - query -> route -> fetch mock data -> analyze -> answer
- Test DynamoDB checkpointer with DynamoDB Local.

### Contract Tests
- Validate Bedrock JSON outputs against Pydantic schemas.
- Validate FastAPI request/response schemas.

### Manual Acceptance Scenarios
- Polish example:
  - "How has the unemployment rate changed in Gdańsk over the last 5 years compared to the national average?"
- US example:
  - "Compare US CPI inflation over the last 3 years with the previous 3-year period."
- Ambiguous query:
  - "What is the unemployment trend?"
- Unsupported query:
  - "Predict tomorrow's stock market."

Deliverable:
- Automated tests prove routing, data normalization, analysis, and state persistence.

## Phase 11 - Observability and Cost Controls
Estimate: 2 days

- Add structured logs for each agent node.
- Log:
  - selected source
  - API endpoint called
  - HTTP status
  - latency
  - Bedrock token usage
  - DynamoDB read/write counts
- Add CloudWatch dashboards for:
  - request rate
  - error rate
  - p95 latency
  - LLM cost per day
- Add simple cost guardrails:
  - limit maximum conversation turns
  - cache normalized API responses briefly if appropriate
  - use Haiku only, not larger models

Deliverable:
- You can monitor performance, errors, and AWS/Bedrock costs.

## Phase 12 - Documentation and Demo Preparation
Estimate: 2 days

- Write README with:
  - architecture diagram
  - local setup instructions
  - AWS deployment steps
  - required secrets
  - example queries
- Add architecture diagram showing:
  - React frontend on S3/CloudFront
  - FastAPI backend in ECS/Fargate
  - LangGraph agents
  - Bedrock Claude Haiku
  - DynamoDB state
  - Secrets Manager
  - GUS and FRED APIs
- Prepare demo script:
  1. Ask a Polish regional question.
  2. Show router selecting GUS.
  3. Show API Engineer constructing the request.
  4. Show analyst returning comparison answer.
  5. Ask a US macro question.
  6. Show FRED routing and response.
- Prepare interview talking points:
  - deterministic data grounding
  - multi-agent state management
  - secure secrets handling
  - low-cost AWS deployment

Deliverable:
- Project is presentable, reproducible, and easy to explain in an interview.

## Suggested Build Order
1. Phase 0 - Repository and local baseline
2. Phase 1 - Domain models and API contract
3. Phase 2 - GUS and FRED adapters
4. Phase 3 - LangGraph workflow with mocked LLM first
5. Phase 4 - Bedrock integration
6. Phase 5 - DynamoDB state persistence
7. Phase 6 - Secrets Manager and IAM security
8. Phase 7 - FastAPI hardening
9. Phase 8 - React frontend
10. Phase 9 - AWS infrastructure deployment
11. Phase 10 - Testing and acceptance scenarios
12. Phase 11 - Observability and cost controls
13. Phase 12 - Documentation and demo

## Minimum Viable Version
For the fastest working prototype, build only:
- FastAPI backend
- GUS adapter
- FRED adapter
- LangGraph router + API engineer + analyst using Bedrock Haiku
- In-memory state instead of DynamoDB
- Simple React chat UI or curl-based testing

Then add production features in this order:
1. DynamoDB persistence
2. Secrets Manager integration
3. Docker deployment
4. S3/CloudFront frontend hosting
5. Observability and cost dashboards
