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
    Write-Output "MODULE_IMPORTED=NO"
    Write-Output "SEARCHED="
    foreach ($p in $candidates) { Write-Output "  $p" }
    Write-Output "--- DERIVED P29B_RESULT ---"
    Write-Output "P29B_RESULT=MODULE_NOT_IMPORTED"
    Write-Output "(no log at all: the p29b module body never executed an import-time statement"
    Write-Output " in-game -- it was packaged but not auto-imported.  This is distinct from"
    Write-Output " 'module ran but hook missed', which would show a log containing"
    Write-Output " P29B_MODULE_IMPORTED=YES and P29B_RESULT=HOOK_NOT_INSTALLED.  Compare the"
    Write-Output " ts4script layout to the working ww_p29_tuning_debug.ts4script, re-run"
    Write-Output " deploy, launch the game so the import fires, quit, then re-run read_log.)"
    exit 0
}
Write-Output "LOG_FILE=$found"
$lines = Get-Content -LiteralPath $found
if (-not $lines) { Write-Output "LOG_CONTENT=(empty)" } else { foreach ($ln in $lines) { Write-Output $ln } }
Write-Output "--- DERIVED P29B_RESULT ---"
$r = @(& python $report $found 2>&1)
foreach ($rl in $r) { Write-Output $rl }
exit 0
