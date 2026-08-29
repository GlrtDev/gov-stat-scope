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
./scripts/test.sh single tests/integration/test_fred_integration.py

# Run a single test file using the PowerShell script (Windows)
.\scripts\test.ps1 -TestType single -TestPath "tests\integration\test_fred_integration.py"