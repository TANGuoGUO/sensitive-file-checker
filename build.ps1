param(
    [string]$PythonPath = "",
    [ValidateSet("onefile", "portable", "both")][string]$Layout = "both",
    [string]$OutputDirectory = "dist"
)

$ErrorActionPreference = "Stop"
$PreviousPyInstallerConfig = $env:PYINSTALLER_CONFIG_DIR
$PreviousBuildLayout = $env:SFC_BUILD_LAYOUT
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
    & $PythonExecutable @PythonPrefix "check_build_dependencies.py"
    if ($LASTEXITCODE -ne 0) {
        throw "Build dependencies are missing or PDF AES verification failed. Install them manually: python -m pip install -r requirements.txt"
    }
    $Layouts = if ($Layout -eq "both") { @("onefile", "portable") } else { @($Layout) }
    foreach ($CurrentLayout in $Layouts) {
        $env:SFC_BUILD_LAYOUT = $CurrentLayout
        & $PythonExecutable @PythonPrefix -m PyInstaller --noconfirm --clean --workpath "build\$CurrentLayout" --distpath $OutputDirectory SensitiveFileChecker.spec
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller $CurrentLayout build failed with exit code $LASTEXITCODE."
        }
    }
    if ($Layout -ne "portable" -and -not (Test-Path -LiteralPath (Join-Path $OutputDirectory "SensitiveFileChecker.exe") -PathType Leaf)) {
        throw "PyInstaller finished without producing SensitiveFileChecker.exe."
    }
    if ($Layout -ne "onefile") {
        $PortableRoot = Join-Path $OutputDirectory "SensitiveFileChecker-portable"
        if (-not (Test-Path -LiteralPath (Join-Path $PortableRoot "SensitiveFileChecker.exe") -PathType Leaf)) {
            throw "Portable executable was not produced."
        }
        Copy-Item -LiteralPath "diagnose.ps1", "diagnose.cmd", "README.md", "sample-rules.json" -Destination $PortableRoot -Force
    }
    Write-Host "Build completed. Layout: $Layout; output: $OutputDirectory"
} finally {
    Pop-Location
    $env:PYINSTALLER_CONFIG_DIR = $PreviousPyInstallerConfig
    $env:SFC_BUILD_LAYOUT = $PreviousBuildLayout
}
