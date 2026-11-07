<#
.SYNOPSIS
    Probe a session log with DSH's real load path instead of guessing.

.DESCRIPTION
    `dsh headless --session-id <id>` adopts an existing session, which means it
    decodes the whole log first. A malformed log therefore fails with
    "stored session ... is corrupt: SessionFormatError: ..." before any model is
    involved.

    A *successful* decode surfaces as a different, unrelated message, e.g.
    'runs under agent preset "standard", which the one-shot runner does not
    compose'. Treat that as PASS: it proves the log parsed.

.PARAMETER SessionId
    One or more session ids (or a file that lists them, one per line).

.PARAMETER DshCommand
    Path to dsh.cmd when it is not on PATH.

.EXAMPLE
    .\probe_session.ps1 -SessionId session-aaaa,session-bbbb
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string[]]$SessionId,
    [string]$DshCommand,
    [string]$WorkingDirectory = (Get-Location).Path
)

$dsh = if ($DshCommand) { $DshCommand } else { 'dsh' }
$ids = @()
foreach ($item in $SessionId) {
    if (Test-Path -LiteralPath $item) {
        $ids += Get-Content -LiteralPath $item | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() }
    } else {
        $ids += $item
    }
}

$failed = 0
foreach ($id in $ids) {
    Push-Location -LiteralPath $WorkingDirectory
    try {
        $out = & $dsh headless --session-id $id 'x' 2>&1 | Out-String
    } finally {
        Pop-Location
    }

    if ($out -match 'is corrupt' -or $out -match 'SessionFormatError') {
        $firstLine = ($out -split "`n" | Where-Object { $_ -match 'dsh:' } | Select-Object -First 1)
        Write-Host ("FAIL {0}`n     {1}" -f $id, $firstLine.Trim()) -ForegroundColor Red
        $failed++
    } elseif ($out -match 'agent preset' -or $out -match 'does not compose') {
        Write-Host ("PASS {0}  (log decoded; headless cannot compose the preset)" -f $id) -ForegroundColor Green
    } elseif ($out -match 'unknown session' -or $out -match 'no such session') {
        Write-Host ("FAIL {0}  (unknown session id)" -f $id) -ForegroundColor Red
        $failed++
    } else {
        $firstLine = ($out -split "`n" | Where-Object { $_.Trim() } | Select-Object -First 1)
        Write-Host ("WARN {0}  {1}" -f $id, $firstLine.Trim()) -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host ("probed {0} session(s), {1} failed" -f $ids.Count, $failed)
if ($failed -gt 0) { exit 1 }
