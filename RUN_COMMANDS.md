# Running tests on Windows
# Option 1: Run with Git Bash or WSL
bash ./scripts/test.sh unit
bash ./scripts/test.sh integration
bash ./scripts/test.sh all

# Option 2: Run natively via PowerShell
.\scripts\test.ps1 -TestType unit
.\scripts\test.ps1 -TestType integration
.\scripts\test.ps1 -TestType all