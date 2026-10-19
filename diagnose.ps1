# This script is intentionally ASCII so Windows PowerShell 5.1 reads it reliably.
# It requires no Python, installs nothing, and never changes PATH or uploads data.
param([string]$OutputDirectory = "")

$ErrorActionPreference = "Stop"
# Resolve the built-in module explicitly when a parent shell supplied a custom
# PSModulePath. This does not change PATH or install anything.
try {
    Import-Module -Name (Join-Path $PSHOME "Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1") -ErrorAction Stop
} catch { }
$ReportLines = New-Object 'System.Collections.Generic.List[string]'
$ReportLines.Add("Sensitive File Checker - launch diagnostics")
$ReportLines.Add("GeneratedUtc=" + [DateTime]::UtcNow.ToString("o"))
$ReportLines.Add("PowerShellVersion=" + $PSVersionTable.PSVersion.ToString())
$ReportLines.Add("Is64BitOperatingSystem=" + [Environment]::Is64BitOperatingSystem)
$ReportLines.Add("Is64BitProcess=" + [Environment]::Is64BitProcess)
$ReportLines.Add("OSVersionFallback=" + [Environment]::OSVersion.Version.ToString())

function Add-SafeError([string]$Prefix, $Exception) {
    # Exception messages can contain a user profile path. Record only error types
    # and codes, plus the generic OS error text (which has no executable path).
    $ReportLines.Add($Prefix + ".Type=" + $Exception.GetType().FullName)
    $ReportLines.Add($Prefix + ".HResult=" + $Exception.HResult)
    $CurrentException = $Exception
    while ($null -ne $CurrentException) {
        if ($CurrentException -is [System.ComponentModel.Win32Exception]) {
            $NativeCode = $CurrentException.NativeErrorCode
            $ReportLines.Add($Prefix + ".NativeErrorCode=" + $NativeCode)
            $GenericError = New-Object System.ComponentModel.Win32Exception -ArgumentList $NativeCode
            $ReportLines.Add($Prefix + ".SystemMessage=" + $GenericError.Message)
            break
        }
        $CurrentException = $CurrentException.InnerException
    }
}

if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $PSScriptRoot ("diagnostic-" + (Get-Date -Format "yyyyMMdd-HHmmss-fff"))
}
try {
    $OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
    [void][System.IO.Directory]::CreateDirectory($OutputDirectory)
} catch {
    Write-Host "Cannot create diagnostic folder. Move this tool to a writable folder and try again."
    exit 1
}
$ReportPath = Join-Path $OutputDirectory "launcher-report.txt"
$Utf8WithBom = New-Object System.Text.UTF8Encoding -ArgumentList $true

function Save-LauncherReport {
    [System.IO.File]::WriteAllLines($ReportPath, $ReportLines.ToArray(), $Utf8WithBom)
}

try {
    $OperatingSystem = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop |
        Select-Object -First 1 Caption, Version, BuildNumber, OSArchitecture
    foreach ($Property in @("Caption", "Version", "BuildNumber", "OSArchitecture")) {
        $ReportLines.Add("OS." + $Property + "=" + $OperatingSystem.$Property)
    }
} catch {
    Add-SafeError "OSQueryError" $_.Exception
    $ReportLines.Add("OS.Caption=Windows (CIM query unavailable)")
    $ReportLines.Add("OS.Version=" + [Environment]::OSVersion.Version.ToString())
    $ReportLines.Add("OS.BuildNumber=" + [Environment]::OSVersion.Version.Build)
    $ReportLines.Add("OS.OSArchitecture=" + $(if ([Environment]::Is64BitOperatingSystem) { "64-bit" } else { "32-bit" }))
}

# Only these known locations are considered. Never search other executables.
$ExecutablePath = Join-Path $PSScriptRoot "SensitiveFileChecker.exe"
$ExecutableLocation = "beside-script"
if (-not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
    $ExecutablePath = Join-Path $PSScriptRoot "scan.exe"
    $ExecutableLocation = "beside-script-renamed"
}
if (-not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
    $ExecutablePath = Join-Path $PSScriptRoot "dist\SensitiveFileChecker.exe"
    $ExecutableLocation = "dist"
}
$ReportLines.Add("ExecutableLocation=" + $ExecutableLocation)
$ReportLines.Add("ExecutableName=" + [System.IO.Path]::GetFileName($ExecutablePath))

if (-not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
    $ReportLines.Add("Status=executable_missing")
    $ReportLines.Add("Error=SensitiveFileChecker.exe or scan.exe was not found beside this script, or SensitiveFileChecker.exe in dist.")
    Save-LauncherReport
    Write-Host "Executable not found. Keep diagnose.cmd, diagnose.ps1 and SensitiveFileChecker.exe together."
    Write-Host ("Report folder: " + $OutputDirectory)
    exit 1
}

$IntegrityMismatch = $false
$ExecutableHash = ""
try {
    $ExecutableInfo = Get-Item -LiteralPath $ExecutablePath
    $ReportLines.Add("ExecutableBytes=" + $ExecutableInfo.Length)
    try {
        $ExecutableHash = (Get-FileHash -LiteralPath $ExecutablePath -Algorithm SHA256).Hash
    } catch {
        # Older or restricted PowerShell module search paths may hide Get-FileHash.
        $HashStream = [System.IO.File]::OpenRead($ExecutablePath)
        $Hasher = [System.Security.Cryptography.SHA256]::Create()
        try {
            $ExecutableHash = [BitConverter]::ToString($Hasher.ComputeHash($HashStream)).Replace("-", "")
        } finally {
            $HashStream.Dispose()
            $Hasher.Dispose()
        }
    }
    $ReportLines.Add("ExecutableSHA256=" + $ExecutableHash)
} catch {
    Add-SafeError "ExecutableReadError" $_.Exception
}

# Check the release manifest's original name even when the EXE is renamed scan.exe.
$ChecksumPath = Join-Path ([System.IO.Path]::GetDirectoryName($ExecutablePath)) "SHA256SUMS.txt"
if (-not (Test-Path -LiteralPath $ChecksumPath -PathType Leaf)) {
    $ChecksumPath = Join-Path $PSScriptRoot "SHA256SUMS.txt"
}
if (Test-Path -LiteralPath $ChecksumPath -PathType Leaf) {
    try {
        $ExpectedHash = ""
        foreach ($ChecksumLine in [System.IO.File]::ReadAllLines($ChecksumPath)) {
            if ($ChecksumLine -match '^\s*([A-Fa-f0-9]{64})\s+\*?(?:.*[\\/])?SensitiveFileChecker\.exe\s*$') {
                $ExpectedHash = $Matches[1]
                break
            }
        }
        if ($ExpectedHash -and $ExecutableHash) {
            $ReportLines.Add("ReleaseExpectedSHA256=" + $ExpectedHash.ToUpperInvariant())
            if ($ExecutableHash -ieq $ExpectedHash) {
                $ReportLines.Add("ReleaseIntegrity=matched")
            } else {
                $IntegrityMismatch = $true
                $ReportLines.Add("ReleaseIntegrity=mismatch")
                $MismatchMessage = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("5paH5Lu25LiO5Y+R6KGM54mI5LiN5LiA6Ie077yM5Y+v6IO95LiL6L29L+S8oOi+k+S4jeWujOaVtA=="))
                $ReportLines.Add("IntegrityError=" + $MismatchMessage)
                $ReportLines.Add("IntegrityErrorEnglish=File differs from the release; download or transfer may be incomplete.")
                Write-Host $MismatchMessage
            }
        } else {
            $ReportLines.Add("ReleaseIntegrity=unverified (original EXE entry or executable hash unavailable)")
        }
    } catch {
        Add-SafeError "ChecksumReadError" $_.Exception
    }
} else {
    $ReportLines.Add("ReleaseIntegrity=unverified (SHA256SUMS.txt not supplied)")
}

$ReportLines.Add("Status=launching_diagnostics")
# This file exists before starting the EXE, including when Windows rejects it.
Save-LauncherReport
$ScriptExitCode = 1
try {
    Write-Host "Running local startup checks. A short diagnostic window may appear."
    $QuotedOutput = '"' + $OutputDirectory + '"'
    $DiagnosticProcess = Start-Process -FilePath $ExecutablePath `
        -ArgumentList @("--diagnostics", "--output", $QuotedOutput) `
        -RedirectStandardOutput (Join-Path $OutputDirectory "bootloader-stdout.log") `
        -RedirectStandardError (Join-Path $OutputDirectory "bootloader-stderr.log") `
        -WorkingDirectory $PSScriptRoot -PassThru -Wait -ErrorAction Stop
    $DiagnosticProcess.Refresh()
    $ProcessExitCode = $DiagnosticProcess.ExitCode
    $ReportLines.Add("ExitCode=" + $ProcessExitCode)
    if ($null -ne $ProcessExitCode) {
        $ExitHex = ([int64]$ProcessExitCode -band 0xFFFFFFFFL).ToString("X8")
        $ReportLines.Add("ExitCodeHex=0x" + $ExitHex)
    }
    $LauncherReportPresent = Test-Path -LiteralPath (Join-Path $OutputDirectory "report.json") -PathType Leaf
    $ReportLines.Add("LauncherReportPresent=" + $LauncherReportPresent)
    if ($ProcessExitCode -eq 0 -and $LauncherReportPresent -and -not $IntegrityMismatch) {
        $ReportLines.Add("Status=diagnostics_finished")
        $ScriptExitCode = 0
    } else {
        $ReportLines.Add("Status=diagnostics_failed")
        if (-not $LauncherReportPresent) {
            $ReportLines.Add("Error=No report.json was produced; the application launcher may not have started. See bootloader-stderr.log.")
        } else {
            $ReportLines.Add("Error=Diagnostics or release integrity check failed. See diagnostic files.")
        }
    }
} catch {
    $ReportLines.Add("Status=launch_failed")
    Add-SafeError "LaunchError" $_.Exception
} finally {
    try {
        # Read only the names and sizes of files produced by this diagnostic run.
        # Do not copy their contents into the launcher report.
        foreach ($DiagnosticFile in Get-ChildItem -LiteralPath $OutputDirectory -File) {
            if ($DiagnosticFile.Name -ne "launcher-report.txt") {
                $ReportLines.Add("DiagnosticFile=" + $DiagnosticFile.Name + ";Bytes=" + $DiagnosticFile.Length)
            }
        }
        Save-LauncherReport
    } catch {
        Write-Host "Could not finish writing the report. Check the diagnostic folder."
        $ScriptExitCode = 1
    }
}

Write-Host ("Report folder: " + $OutputDirectory)
Write-Host "Keep the whole diagnostic folder when asking for help. No data was uploaded."
exit $ScriptExitCode
