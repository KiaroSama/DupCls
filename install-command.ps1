# Adds this folder to the current user's PATH so `DupCls` can be run
# from a new PowerShell, Windows Terminal, or cmd.exe session.

$ErrorActionPreference = "Stop"

$ScriptDir = if ($PSScriptRoot) {
    $PSScriptRoot
}
else {
    Split-Path -Parent $MyInvocation.MyCommand.Path
}

$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$PathParts = @()
if (![string]::IsNullOrWhiteSpace($UserPath)) {
    $PathParts = $UserPath -split ";" | Where-Object { $_ -and $_.Trim() }
}

$AlreadyInstalled = $false
foreach ($Part in $PathParts) {
    if ([string]::Equals($Part.TrimEnd([char]"\"), $ScriptDir.TrimEnd([char]"\"), [StringComparison]::OrdinalIgnoreCase)) {
        $AlreadyInstalled = $true
        break
    }
}

if (!$AlreadyInstalled) {
    $NewPathParts = @($PathParts) + $ScriptDir
    [Environment]::SetEnvironmentVariable("Path", ($NewPathParts -join ";"), "User")
    Write-Host "Added to user PATH: $ScriptDir" -ForegroundColor Green
}
else {
    Write-Host "Already in user PATH: $ScriptDir" -ForegroundColor Yellow
}

if (($env:Path -split ";") -notcontains $ScriptDir) {
    $env:Path = "$env:Path;$ScriptDir"
}

Write-Host "Open a new terminal and run: DupCls" -ForegroundColor Cyan
