# Running tests
# Option 1: Run with Bash, Git Bash or WSL
```
bash ./scripts/test.sh unit
bash ./scripts/test.sh integration
bash ./scripts/test.sh all
```
# Option 2: Run natively on Windows via PowerShell
```
.\scripts\test.ps1 -TestType unit
.\scripts\test.ps1 -TestType integration
.\scripts\test.ps1 -TestType all
```

# Run a single test file using the Bash script (Mac/Linux/WSL)
```
./scripts/test.sh single tests/integration/test_fred_integration.py
```
# Run a single test file using the PowerShell script (Windows)
```
.\scripts\test.ps1 -TestType single -TestPath "tests\integration\test_gus_deep_flow.py"
```


# Running backend + dynamoDB for local dev
```
.\scripts\run_backend.ps1
```
or
```
chmod +x run_backend.sh
.\scripts\run_backend.sh
```

# Testing bdl endpoints
```
python bdl_swagger_client.py list
python bdl_swagger_client.py call "/aggregates/{id}" --param id=1
python bdl_swagger_client.py interactive
python bdl_swagger_client.py interactive --api-key "1233456-1111-4444-9999-00000000000"
```