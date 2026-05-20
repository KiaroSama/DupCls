param(
    [switch]$NoPause,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ScriptArgs
)

# PowerShell launcher for DupCls.
# It runs DupCls.py from this folder and falls back from py.exe to python.exe
# when the Python launcher is installed but has no registered interpreter.

$ErrorActionPreference = "Stop"

try {
    $Host.UI.RawUI.WindowTitle = "DupCls"
}
catch {
    # Some hosts (non-console, ISE-like) do not allow setting the window title.
}

function Write-Color {
    param(
        [string]$Text,
        [ConsoleColor]$Color = [ConsoleColor]::White
    )
    try {
        Write-Host $Text -ForegroundColor $Color
    }
    catch {
        Write-Host $Text
    }
}

function Clear-HostSafe {
    try {
        Clear-Host
    }
    catch {
        # Some hosts (no real console) cannot move the cursor. Ignore.
    }
}

function Wait-BeforeExit {
    if ($NoPause) {
        return
    }

    Write-Host ""
    Write-Host "Press any key to close..."
    try {
        $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    }
    catch {
        try {
            $null = Read-Host
        }
        catch {
            # Last-resort: no interactive input available, just return.
        }
    }
}

function Test-PythonCandidate {
    param(
        [string]$Source,
        [string[]]$ProbeArgs
    )

    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $Source @ProbeArgs -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" *> $null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
}

$ScriptDir = if ($PSScriptRoot) {
    $PSScriptRoot
}
else {
    Split-Path -Parent $MyInvocation.MyCommand.Path
}

$PythonScript = Join-Path $ScriptDir "DupCls.py"

Clear-HostSafe
Write-Color "============================================================" Cyan
Write-Color "  DupCls Launcher" Cyan
Write-Color "============================================================" Cyan
Write-Host ""

if (!(Test-Path -LiteralPath $PythonScript)) {
    Write-Color "Python script not found:" Red
    Write-Color $PythonScript Red
    Wait-BeforeExit
    exit 1
}

Set-Location -LiteralPath $ScriptDir

$Candidates = @(
    @{ Name = "py"; ProbeArgs = @("-3"); RunArgs = @("-3") },
    @{ Name = "python"; ProbeArgs = @(); RunArgs = @() },
    @{ Name = "python3"; ProbeArgs = @(); RunArgs = @() }
)

$LocalPythonRoot = Join-Path $env:LocalAppData "Programs\Python"
if (Test-Path -LiteralPath $LocalPythonRoot) {
    $LocalPythonExecutables = Get-ChildItem -LiteralPath $LocalPythonRoot -Directory -Filter "Python*" |
        Sort-Object -Property Name -Descending |
        ForEach-Object { Join-Path $_.FullName "python.exe" } |
        Where-Object { Test-Path -LiteralPath $_ }

    foreach ($PythonExe in $LocalPythonExecutables) {
        $Candidates += @{ Path = $PythonExe; ProbeArgs = @(); RunArgs = @() }
    }
}

$ExitCode = 1
$Ran = $false

try {
    foreach ($Candidate in $Candidates) {
        if ($Candidate.ContainsKey("Path")) {
            $PythonSource = $Candidate.Path
        }
        else {
            $Command = Get-Command $Candidate.Name -ErrorAction SilentlyContinue
            if (!$Command) {
                continue
            }
            $PythonSource = $Command.Source
        }

        [string[]]$ProbeArgs = $Candidate.ProbeArgs
        [string[]]$RunArgs = $Candidate.RunArgs

        if (!(Test-PythonCandidate -Source $PythonSource -ProbeArgs $ProbeArgs)) {
            continue
        }

        $PreviousErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & $PythonSource @RunArgs $PythonScript @ScriptArgs
            $ExitCode = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
        }
        finally {
            $ErrorActionPreference = $PreviousErrorActionPreference
        }

        $Ran = $true
        break
    }

    if (!$Ran) {
        Write-Color "Python 3.10+ was not found. Install Python and try again." Red
        $ExitCode = 1
    }
}
catch {
    Write-Color "Launcher error:" Red
    Write-Color $_.Exception.Message Red
    $ExitCode = 1
}

Wait-BeforeExit
exit $ExitCode
