<#
.SYNOPSIS
    Restart DSH so it reloads the workspace registry from disk.

.DESCRIPTION
    Two non-obvious rules are baked in:

    1. The registry is read once at process start, so a restart is mandatory
       after editing it.
    2. The app must be *hard terminated*. A graceful quit flushes the in-memory
       (pre-migration) registry over the file and silently discards the import.

    When this script is launched from inside DSH itself, spawn it detached:
    `Invoke-CimMethod Win32_Process Create` parents it to WmiPrvSE, which
    survives the kill, and it sleeps first so the current turn can finish.

.PARAMETER ExePath
    DeepSeek Harness executable.

.PARAMETER ProcessName
    Process name to stop/relaunch (default "DeepSeek Harness").

.PARAMETER RegistryPath
    workspace.json to back up and verify after restart.

.PARAMETER DelaySeconds
    Wait before touching the app (default 20) — lets an in-flight chat persist.

.EXAMPLE
    .\restart_dsh.ps1 -ExePath "$env:LOCALAPPDATA\Programs\DeepSeek Harness\DeepSeek Harness.exe"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ExePath,
    [string]$ProcessName = 'DeepSeek Harness',
    [string]$RegistryPath = "$HOME/.dsh/storages/workspace.json",
    [int]$DelaySeconds = 20,
    [string]$LogPath = "$PSScriptRoot/restart-log.txt"
)

function Write-Log([string]$message) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

Write-Log 'restart helper started'
Start-Sleep -Seconds $DelaySeconds

if (Test-Path -LiteralPath $RegistryPath) {
    $backup = "$RegistryPath.before-restart"
    Copy-Item -LiteralPath $RegistryPath -Destination $backup -Force -ErrorAction SilentlyContinue
    Write-Log "registry backed up -> $backup"
}

# Hard stop: a graceful quit would flush the stale in-memory registry.
for ($round = 1; $round -le 3; $round++) {
    $procs = @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue)
    if ($procs.Count -eq 0) { break }
    Write-Log ("stopping {0} process(es), round {1}" -f $procs.Count, $round)
    $procs | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
}
for ($i = 0; $i -lt 40; $i++) {
    if (-not (Get-Process -Name $ProcessName -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 500
}
Write-Log 'all app processes gone'

for ($attempt = 1; $attempt -le 2; $attempt++) {
    Start-Process -FilePath $ExePath
    Write-Log ("relaunch attempt {0} issued" -f $attempt)
    Start-Sleep -Seconds 25
    if (Get-Process -Name $ProcessName -ErrorAction SilentlyContinue) { break }
    Write-Log 'no process after relaunch; retrying'
    Get-Process -Name $ProcessName -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 5
}
Start-Sleep -Seconds 12

if (Test-Path -LiteralPath $RegistryPath) {
    try {
        $registry = Get-Content -LiteralPath $RegistryPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $order = @($registry.global.workspaceIds)
        $table = @($registry.tables.workspaces.PSObject.Properties)
        Write-Log ("registry: order={0} table={1}" -f $order.Count, $table.Count)
        foreach ($prop in $table) {
            Write-Log ("  {0}: {1} session(s)" -f $prop.Value.title, @($prop.Value.sessionIds).Count)
        }
    } catch {
        Write-Log ("registry unreadable after restart: " + $_.Exception.Message)
    }
}

$unitDir = Join-Path (Split-Path -Parent $RegistryPath) 'workspace/workspaces'
if (Test-Path -LiteralPath $unitDir) {
    $count = @(Get-ChildItem -LiteralPath $unitDir -Filter *.json -ErrorAction SilentlyContinue).Count
    Write-Log ("per-record layout materialised: {0} file(s)" -f $count)
} else {
    Write-Log 'per-record layout not materialised (host still reads the whole-unit file)'
}

Write-Log ("app running: {0}" -f [bool](Get-Process -Name $ProcessName -ErrorAction SilentlyContinue))
Write-Log 'done'
