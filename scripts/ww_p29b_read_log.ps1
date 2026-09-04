# ww_p29b_read_log.ps1 -- P29-B: read the runtime display/picker trace log (real).
# Dumps ww_p29b_display_trace.log from the writable roots the mod uses, then prints
# the derived run-level P29B_RESULT via ww_p29b_report_check.py (HOOK_ERROR wins ->
# INVALID_HOOK_ERROR).  ASCII-only.
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

$name = "ww_p29b_display_trace.log"
$candidates = @(
    (Join-Path $env:TEMP $name),
    (Join-Path $env:TMP $name),
    (Join-Path ([System.Environment]::GetFolderPath('UserProfile')) $name),
    (Join-Path (Get-Location).Path $name)
)
$found = $null
foreach ($p in $candidates) {
    if (Test-Path -LiteralPath $p -PathType Leaf) {
        if ($found) { continue }
        $found = $p
    }
}
Write-Output "=== P29-B LOG ==="
$report = Join-Path $PSScriptRoot "ww_p29b_report_check.py"
if (-not $found) {
    Write-Output "LOG_ENTRY=NOT_FOUND"
    Write-Output "SEARCHED="
    foreach ($p in $candidates) { Write-Output "  $p" }
    Write-Output "(no log yet: launch game with the p29b ts4script, open Nevely picker, quit, re-run.)"
    exit 0
}
Write-Output "LOG_FILE=$found"
$lines = Get-Content -LiteralPath $found
if (-not $lines) { Write-Output "LOG_CONTENT=(empty)" } else { foreach ($ln in $lines) { Write-Output $ln } }
Write-Output "--- DERIVED P29B_RESULT ---"
$r = @(& python $report $found 2>&1)
foreach ($rl in $r) { Write-Output $rl }
exit 0
