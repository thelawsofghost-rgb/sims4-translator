# ww_p29_tuning_rollback.ps1 -- P29-TUNING rollback (Windows real machine).
#
# Removes ONLY P29-TUNING effects:
#   1. Delete Mods\ww_p29_tuning_debug.ts4script
#   2. Delete the tuning log if ours (ww_p29_tuning_trace.log under the writable
#      roots the mod uses)
#   3. If this P29-TUNING deploy re-deployed P28C (flag file present), invoke the
#      P28C rollback so its TEST299 override + Resource.cfg edit are removed and
#      the base is restored to the pre-test clean state.
#   4. Delete the flag.
# Never touches any other mod / source WW / Nevely / P27/P28A/P28B artifacts.
# ASCII-only; $PyArgs never $Args; no 2>&1.
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

$MODS          = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods"
$WORKSPACE     = "D:\projects\sims4_trans"
$DEBUG_TS4     = Join-Path $MODS "ww_p29_tuning_debug.ts4script"
$LOG_NAME      = "ww_p29_tuning_trace.log"
$FLAG_P28C_DEP = Join-Path $WORKSPACE "dist\ww_p29_tuning_deployed_p28c.flag"
$P28C_ROLLBACK = Join-Path $WORKSPACE "scripts\ww_p28c_rollback.ps1"

function Fail($reason) { Write-Output "VERDICT=ROLLBACK_FAIL"; Write-Output "REASON=$reason"; exit 1 }

Write-Output "=== P29-TUNING ROLLBACK ==="

# 1. debug ts4script
if (Test-Path -LiteralPath $DEBUG_TS4) {
    Remove-Item -LiteralPath $DEBUG_TS4 -Force
    if (Test-Path -LiteralPath $DEBUG_TS4) { Fail "debug ts4 delete failed" }
    Write-Output "DEBUG_TS4=DELETED"
} else {
    Write-Output "DEBUG_TS4=NOT_PRESENT"
}

# 2. tuning log under the roots the mod may use
foreach ($root in @($env:TEMP, $env:TMP, ([System.Environment]::GetFolderPath('UserProfile')), (Get-Location).Path)) {
    if (-not $root) { continue }
    $lf = Join-Path $root $LOG_NAME
    if (Test-Path -LiteralPath $lf -PathType Leaf) {
        Remove-Item -LiteralPath $lf -Force
        Write-Output "LOG=DELETED $lf"
    }
}

# 3. P28C flag -> restore
if (Test-Path -LiteralPath $FLAG_P28C_DEP) {
    Write-Output "--- P28C ROLLBACK (restore base removed by this run) ---"
    if (Test-Path -LiteralPath $P28C_ROLLBACK) {
        & $P28C_ROLLBACK
        if ($LASTEXITCODE -ne 0) { Fail "P28C rollback failed after tuning" }
    } else {
        Write-Output "WARN: P28C rollback script missing; leaving P28C in place -- verify manually."
    }
    Remove-Item -LiteralPath $FLAG_P28C_DEP -Force
    Write-Output "P28C_STEP=ROLLED_BACK"
} else {
    Write-Output "P28C_STEP=NOT_TOUCHED (no redep flag)"
}

Write-Output "VERDICT=ROLLED_BACK"
exit 0
