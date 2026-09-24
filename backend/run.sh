#!/bin/bash
path=$(echo "$0" | sed 's/\.[^.]*$//')
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --proxy-headers --forwarded-allow-ips='*'