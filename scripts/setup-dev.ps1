# Creates the development environment outside OneDrive (see docs/adr/0002).
#   - virtual environment in %LOCALAPPDATA%\Tabularium\venv
#   - project installed in editable mode with dev dependencies
#   - git hooks (pre-commit + commit-msg)
#
# Run it from a normal PowerShell window. (Inside sandboxed/packaged apps, %LOCALAPPDATA% may be
# redirected to a private location that the rest of Windows cannot see.)
$ErrorActionPreference = "Stop"

function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments)
    # Windows PowerShell 5.1 turns native stderr into errors; judge by exit code instead.
    $ErrorActionPreference = "Continue"
    & $Exe @Arguments 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { throw "$Exe $Arguments failed with exit code $LASTEXITCODE" }
}

$repo = Split-Path -Parent $PSScriptRoot
$homeDir = Join-Path $env:LOCALAPPDATA "Tabularium"
$venv = Join-Path $homeDir "venv"
$py = Join-Path $venv "Scripts\python.exe"

New-Item -ItemType Directory -Force $homeDir | Out-Null

if (-not (Test-Path $py)) {
    Write-Host "Creating virtual environment in $venv"
    Invoke-Native "python" @("-m", "venv", $venv)
}

Invoke-Native $py @("-m", "pip", "install", "--upgrade", "pip")
Invoke-Native $py @("-m", "pip", "install", "-e", "$repo[dev]")

# Windows Smart App Control may refuse SQLAlchemy's unsigned compiled speed-ups ("An Application
# Control policy has blocked this file"). The pure-Python build behaves identically.
$saVersion = ((& $py -m pip show sqlalchemy) | Select-String "^Version: ").ToString().Split(" ")[1]
$env:DISABLE_SQLALCHEMY_CEXT = "1"
Invoke-Native $py @(
    "-m", "pip", "install", "--force-reinstall", "--no-deps", "--no-binary", "sqlalchemy",
    "sqlalchemy==$saVersion"
)
Remove-Item Env:\DISABLE_SQLALCHEMY_CEXT

Push-Location $repo
try {
    Invoke-Native $py @("-m", "pre_commit", "install", "--install-hooks")
} finally {
    Pop-Location
}

# The interface is a React app built with Node.js into src/tabularium/web/spa (ADR 0010).
if (Get-Command npm -ErrorAction SilentlyContinue) {
    Push-Location (Join-Path $repo "frontend")
    try {
        Invoke-Native "npm" @("ci", "--no-audit", "--no-fund")
        Invoke-Native "npm" @("run", "build")
    } finally {
        Pop-Location
    }
} else {
    Write-Host ""
    Write-Host "Node.js not found: the interface is not built." -ForegroundColor Yellow
    Write-Host "     Install it (winget install OpenJS.NodeJS.LTS) and run this script again."
}

$denylist = Join-Path $homeDir "denylist.txt"
if (-not (Test-Path $denylist)) {
    Write-Host ""
    Write-Host "REQUIRED before committing: create $denylist" -ForegroundColor Yellow
    Write-Host "     with one personal word per line (name, family names, e-mail...)."
    Write-Host "     Commits containing them are blocked; without the file all commits are blocked."
}

Write-Host ""
Write-Host "Done. Python: $py"
