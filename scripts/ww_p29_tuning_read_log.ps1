# ww_p29_tuning_read_log.ps1 -- P29-TUNING: read the runtime trace log (real machine).
#
# After a game run with ww_p29_tuning_debug.ts4script installed, dump the trace the
# mod wrote.  The mod logs to the FIRST writable of: %TEMP%, %TMP%, ~, cwd
# as  ww_p29_tuning_trace.log .  We search the known roots in the same order and
# print whichever exists (there should be exactly one).  ASCII-only.
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

$name = "ww_p29_tuning_trace.log"
$candidates = @(
    (Join-Path $env:TEMP $name),
    (Join-Path $env:TMP $name),
    (Join-Path ([System.Environment]::GetFolderPath('UserProfile')) $name),
    (Join-Path (Get-Location).Path $name)
)

$found = $null
foreach ($p in $candidates) {
    if (Test-Path -LiteralPath $p -PathType Leaf) {
        if ($found) { continue }   # only report the first existing one
        $found = $p
    }
}

Write-Output "=== P29-TUNING LOG ==="
$report = Join-Path $PSScriptRoot "ww_p29_tuning_report_check.py"
if (-not $found) {
    Write-Output "LOG_ENTRY=NOT_FOUND"
    Write-Output "SEARCHED="
    foreach ($p in $candidates) { Write-Output "  $p" }
    Write-Output "(there is no log yet: launch game with the tuning debug ts4script installed,"
    Write-Output " trigger the Nevely target animation, quit, then re-run this one-key.)"
    exit 0
}
Write-Output "LOG_FILE=$found"
$lines = Get-Content -LiteralPath $found
if (-not $lines) {
    Write-Output "LOG_CONTENT=(empty)"
} else {
    foreach ($ln in $lines) { Write-Output $ln }
}
# derive the runtime verdict from the real captured log (post-session)
Write-Output "--- DERIVED VERDICT ---"
$r = @(& python $report $found 2>&1)
foreach ($rl in $r) { Write-Output $rl }
exit 0
