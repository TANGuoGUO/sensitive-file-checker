param([string]$PythonPath = "")

$ErrorActionPreference = "Stop"
$PreviousPyInstallerConfig = $env:PYINSTALLER_CONFIG_DIR
$env:PYINSTALLER_CONFIG_DIR = Join-Path $PSScriptRoot ".pyinstaller-cache"
Push-Location $PSScriptRoot
try {
    $PythonExecutable = $null
    $PythonPrefix = @()
    $Candidates = @()
    if ($PythonPath) {
        $Candidates += @{ Name = $PythonPath; Prefix = @() }
    } else {
        $Candidates += @{ Name = "python"; Prefix = @() }
        $Candidates += @{ Name = "py"; Prefix = @("-3") }
        $Candidates += @{ Name = "python3"; Prefix = @() }
    }
    foreach ($Candidate in $Candidates) {
        $Command = Get-Command $Candidate.Name -ErrorAction SilentlyContinue
        if (-not $Command) { continue }
        $CandidatePrefix = @($Candidate.Prefix)
        & $Command.Source @CandidatePrefix -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $PythonExecutable = $Command.Source
            $PythonPrefix = $CandidatePrefix
            break
        }
    }
    if (-not $PythonExecutable) {
        throw "Python 3.9 or later was not found. Install Python or use -PythonPath C:\path\python.exe."
    }
    if (-not (Test-Path -LiteralPath "app.py" -PathType Leaf)) {
        throw "app.py was not found in $PSScriptRoot."
    }
    Write-Host "Checking build dependencies with $PythonExecutable ..."
    & $PythonExecutable @PythonPrefix -c "import os, sys; sys.path.insert(0, os.path.join(os.getcwd(), 'vendor')); import PySide6, pypdf, PyInstaller, pythoncom, pywintypes, win32com.client; print('Build dependencies are available.')"
    if ($LASTEXITCODE -ne 0) {
        throw "Build dependencies are missing. Install them manually: python -m pip install -r requirements.txt"
    }
    & $PythonExecutable @PythonPrefix -m PyInstaller --noconfirm --clean SensitiveFileChecker.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
    if (-not (Test-Path -LiteralPath "dist\SensitiveFileChecker.exe" -PathType Leaf)) {
        throw "PyInstaller finished without producing dist\SensitiveFileChecker.exe."
    }
    Write-Host "Build completed: $PSScriptRoot\dist\SensitiveFileChecker.exe"
} finally {
    Pop-Location
    $env:PYINSTALLER_CONFIG_DIR = $PreviousPyInstallerConfig
}
