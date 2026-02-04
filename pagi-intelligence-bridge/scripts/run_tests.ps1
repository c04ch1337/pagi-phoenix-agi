# Run pytest via Poetry without requiring 'poetry' on PATH.
# Usage: .\scripts\run_tests.ps1 [pytest args...]
# Example: .\scripts\run_tests.ps1 -k test_rlm_vertical_codegen -v
$poetryExe = "$env:APPDATA\Python\Python313\Scripts\poetry.exe"
if (-not (Test-Path $poetryExe)) {
    $poetryExe = "poetry"  # fallback if on PATH
}
& $poetryExe run pytest @args
