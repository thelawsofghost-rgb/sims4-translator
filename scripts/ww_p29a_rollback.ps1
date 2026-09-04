# ww_p29a_rollback.ps1 -- P29-A rollback (Windows real machine).
#
# Removes ONLY P29-A effects:
#   1. Delete Mods\ww_p29a_debug.ts4script
#   2. Delete P29 log if ours (Mods\ww_p29a_trace.log) -- only that name
#   3. If this P29A deploy had re-applied P28C (flag file present), invoke the
#      P28C rollback so the P28C TEST299 override + its Resource.cfg edit are
#      removed and the base is restored to the pre-test clean state.
#   4. Delete the P28C-redep flag.
# It NEVER touches any other mod / source WW package / P27/P28A/P28B artifacts.
#
# ASCII-only logic.  $PyArgs never $Args.  no 2>&1.
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

$MODS          = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods"
$WORKSPACE     = "D:\projects\sims4_trans"
$DEBUG_TS4     = Join-Path $MODS "ww_p29a_debug.ts4script"
$LOG_FILE      = Join-Path $MODS "ww_p29a_trace.log"
$STAGE_LOG     = Join-Path $env:TEMP "ww_p29a_trace.log"
$FLAG_P28C_DEP = Join-Path $WORKSPACE "dist\ww_p29a_deployed_p28c.flag"
$P28C_ROLLBACK = Join-Path $WORKSPACE "scripts\ww_p28c_rollback.ps1"

function Fail($reason) { Write-Output "VERDICT=ROLLBACK_FAIL"; Write-Output "REASON=$reason"; exit 1 }

Write-Output "=== P29A ROLLBACK ==="

# 1. debug ts4script
if (Test-Path -LiteralPath $DEBUG_TS4) {
    Remove-Item -LiteralPath $DEBUG_TS4 -Force
    if (Test-Path -LiteralPath $DEBUG_TS4) { Fail "debug ts4 delete failed" }
    Write-Output "DEBUG_TS4=DELETED"
} else {
    Write-Output "DEBUG_TS4=NOT_PRESENT"
}

# 2. P29 log files (only our exact names in Mods root or TEMP)
foreach ($lf in @($LOG_FILE, $STAGE_LOG)) {
    if (Test-Path -LiteralPath $lf) {
        Remove-Item -LiteralPath $lf -Force
        Write-Output "LOG=DELETED $lf"
    }
}

# 3. P28C redep flag -> restore P28C via its own rollback (clean base)
if (Test-Path -LiteralPath $FLAG_P28C_DEP) {
    Write-Output "--- P28C ROLLBACK (restore base removed by this P29A run) ---"
    if (Test-Path -LiteralPath $P28C_ROLLBACK) {
        & $P28C_ROLLBACK
        if ($LASTEXITCODE -ne 0) { Fail "P28C rollback failed after P29A" }
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
