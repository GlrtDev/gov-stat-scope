"""Modular building blocks for the GovData AI Orchestrator service.

The FastAPI entrypoint (`main.py`) assembles a small FastAPI application
from these focused modules rather than defining everything inline:
logging, request/trace ID middleware, global error handling, and the HTTP
endpoints that make up the public API contract.
"""
