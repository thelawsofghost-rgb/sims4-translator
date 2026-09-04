# ww_p29b_rollback.ps1 -- P29-B rollback (Windows real machine).
# Removes ONLY P29-B effects:
#   1. delete Mods\ww_p29b_display_trace.ts4script
#   2. delete the log (ww_p29b_display_trace.log) under the writable roots used
#   3. if this P29-B run redeployed P28C (flag), restore the base via P28C rollback
#   4. delete the flag
# Never touches any other mod / source WW / Nevely / P28C payload / XML / P24.
# ASCII-only; no 2>&1 / no &&.
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

$MODS          = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods"
$WORKSPACE     = "D:\projects\sims4_trans"
$DEBUG_TS4     = Join-Path $MODS "ww_p29b_display_trace.ts4script"
$LOG_NAME      = "ww_p29b_display_trace.log"
$FLAG_P28C_DEP = Join-Path $WORKSPACE "dist\ww_p29b_deployed_p28c.flag"
$P28C_ROLLBACK = Join-Path $WORKSPACE "scripts\ww_p28c_rollback.ps1"

function Fail($reason) { Write-Output "VERDICT=ROLLBACK_FAIL"; Write-Output "REASON=$reason"; exit 1 }

Write-Output "=== P29-B ROLLBACK ==="
if (Test-Path -LiteralPath $DEBUG_TS4) {
    Remove-Item -LiteralPath $DEBUG_TS4 -Force
    if (Test-Path -LiteralPath $DEBUG_TS4) { Fail "debug ts4 delete failed" }
    Write-Output "DEBUG_TS4=DELETED"
} else { Write-Output "DEBUG_TS4=NOT_PRESENT" }

foreach ($root in @($env:TEMP, $env:TMP, ([System.Environment]::GetFolderPath('UserProfile')), (Get-Location).Path)) {
    if (-not $root) { continue }
    $lf = Join-Path $root $LOG_NAME
    if (Test-Path -LiteralPath $lf -PathType Leaf) { Remove-Item -LiteralPath $lf -Force; Write-Output "LOG=DELETED $lf" }
}

if (Test-Path -LiteralPath $FLAG_P28C_DEP) {
    Write-Output "--- P28C ROLLBACK (restore base removed by this run) ---"
    if (Test-Path -LiteralPath $P28C_ROLLBACK) {
        & $P28C_ROLLBACK
        if ($LASTEXITCODE -ne 0) { Fail "P28C rollback failed after p29b" }
    } else {
        Write-Output "WARN P28C rollback missing; leaving P28C -- verify manually."
    }
    Remove-Item -LiteralPath $FLAG_P28C_DEP -Force
    Write-Output "P28C_STEP=ROLLED_BACK"
} else { Write-Output "P28C_STEP=NOT_TOUCHED" }

Write-Output "VERDICT=ROLLED_BACK"
exit 0
