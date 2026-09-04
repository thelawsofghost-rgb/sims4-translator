# ww_p29a_deploy.ps1 -- P29-A deploy (Windows real machine, ONE-KEY).
#
# Goal of the test run (per user, 2026-09-04):
#   With the P28C TEST299 override live AND the P29-A debug ts4script installed,
#   launch the game, open/trigger the Nevely animation list so the related
#   SexAnimationInstance objects finish loading, then exit/read the trace.
#
# What this script does (and only this):
#   1. Build ww_p29a_debug.ts4script under the GAME python (ww_p29a_build_on_win.ps1
#      logic, inlined via Run-Build) -- pyc magic guaranteed to match the game.
#   2. Static-check the hook source (ww_p29a_static_check.py) + offline logic test
#      (ww_p29a_logic_test.py) as build gates, fail-closed.
#   3. Place the debug ts4script into Mods root as  ww_p29a_debug.ts4script
#      (a top-level *.ts4script is auto-imported).  Record its SHA marker.
#   4. Re-apply the ALREADY-VERIFIED P28C TEST299 override by invoking the P28C
#      deploy (keeps its OWN Resource.cfg backup + override; P28C rollback cleans it).
#   5. Print the Windows commands Dorothy runs to collect the log.
#
# Rollback is ww_p29a_rollback.ps1 (removes ONLY P29 ts4script + P29 log/flag, and
# calls P28C rollback ONLY if this deploy had re-applied P28C -- tracked by a flag
# file).  Never touches any other mod / source WW package / P27/P28A/P28B artifacts.
#
# ASCII-only logic; Run-Python wrapper named params ($PyArgs, never $Args); no 2>&1.
param(
    [string]$GamePython = "",
    [switch]$SkipP28C
)
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

$MODS           = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods"
$WORKSPACE      = "D:\projects\sims4_trans"
$SRC_MOD        = Join-Path $WORKSPACE "scripts\ww_p29a_mod.py"
$STATIC         = Join-Path $WORKSPACE "scripts\ww_p29a_static_check.py"
$LOGIC          = Join-Path $WORKSPACE "scripts\ww_p29a_logic_test.py"
$BUILD_WIN      = Join-Path $WORKSPACE "scripts\ww_p29a_build_on_win.ps1"
$DEBUG_TS4      = Join-Path $MODS "ww_p29a_debug.ts4script"
$STAGE          = Join-Path $WORKSPACE "dist\ww_p29a_debug.ts4script"
$FLAG_P28C_DEP  = Join-Path $WORKSPACE "dist\ww_p29a_deployed_p28c.flag"
$P28C_DEPLOY    = Join-Path $WORKSPACE "scripts\ww_p28c_deploy.ps1"
$LOG_ROOT       = Join-Path $MODS "ww_p29a_trace.log"

function Fail($reason) { Write-Output "VERDICT=FAIL"; Write-Output "REASON=$reason"; exit 1 }
function Sha256($p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower() }

function Run-Python {
    param([string]$Script, [string[]]$PyArgs)
    $stderrFile = Join-Path $env:TEMP ("p29a_err_" + [System.Guid]::NewGuid().ToString("N") + ".log")
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & python $Script @PyArgs 2> $stderrFile
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    $errText = ""
    if (Test-Path -LiteralPath $stderrFile) { $errText = Get-Content -LiteralPath $stderrFile -Raw; Remove-Item -LiteralPath $stderrFile -Force }
    return ,@($code, @($out), $errText)
}

Write-Output "=== P29A DEPLOY ==="

# ---------- 0. preflight ----------
if (-not (Test-Path -LiteralPath $MODS))       { Fail "Mods missing: $MODS" }
if (-not (Test-Path -LiteralPath $SRC_MOD))    { Fail "hook source missing: $SRC_MOD" }
if (-not (Test-Path -LiteralPath $STATIC))     { Fail "static_check missing" }
if (-not (Test-Path -LiteralPath $LOGIC))      { Fail "logic_test missing" }
if (-not (Test-Path -LiteralPath $BUILD_WIN))  { Fail "build_on_win missing" }
if ($SkipP28C) {
    Write-Output "P28C_STEP=SKIPPED (-SkipP28C)"
} else {
    if (-not (Test-Path -LiteralPath $P28C_DEPLOY)) { Fail "P28C deploy missing (only needed unless -SkipP28C)" }
}

# ---------- 1. static + logic gates (offline) ----------
Write-Output "--- STATIC GATE ---"
$s = Run-Python -Script $STATIC
if ($s[0] -ne 0) { Write-Output "PY_STDERR=$($s[2])"; Fail "STATIC_FAIL(exit $($s[0]))" }
Write-Output "STATIC=PASS"

Write-Output "--- LOGIC GATE ---"
$l = Run-Python -Script $LOGIC
if ($l[0] -ne 0) { Write-Output "PY_STDERR=$($l[2])"; Fail "LOGIC_FAIL(exit $($l[0]))" }
Write-Output "LOGIC=PASS"

# ---------- 2. build ts4script under game python ----------
Write-Output "--- BUILD (game python) ---"
$b = & $BUILD_WIN -GamePython $GamePython
if ($LASTEXITCODE -ne 0) { Write-Output ($b -join "`n"); Fail "BUILD_ON_WIN_FAIL" }
Write-Output (($b | Where-Object { $_ -like 'VERDICT=*' -or $_ -like 'OUT=*' -or $_ -like 'PY_VER=*' }) -join "`n")
if (-not (Test-Path -LiteralPath $STAGE)) { Fail "stage ts4script not produced" }

# ---------- 3. place debug ts4script ----------
Write-Output "--- PLACE DEBUG TS4 ---"
$preSha = if (Test-Path -LiteralPath $DEBUG_TS4) { Sha256 $DEBUG_TS4 } else { "ABSENT" }
Copy-Item -LiteralPath $STAGE -Destination $DEBUG_TS4 -Force
if (-not (Test-Path -LiteralPath $DEBUG_TS4)) { Fail "copy debug ts4 failed" }
$postSha = Sha256 $DEBUG_TS4
Write-Output "DEBUG_TS4=SHA:$postSha (was $preSha)"
Write-Output "DEBUG_TS4_PATH=$DEBUG_TS4"

# ---------- 4. (optional) re-apply P28C TEST299 override ----------
if ($SkipP28C) {
    Write-Output "P28C=SKIPPED"
} else {
    Write-Output "--- P28C RE-DEPLOY (keeps its own cfg backup) ---"
    & $P28C_DEPLOY
    if ($LASTEXITCODE -ne 0) { Fail "P28C redep failed; run ww_p29a_rollback.ps1 to clean" }
    # remember we re-deployed P28C so rollback can restore it
    if (Test-Path -LiteralPath $FLAG_P28C_DEP) { Remove-Item -LiteralPath $FLAG_P28C_DEP -Force }
    New-Item -Path $FLAG_P28C_DEP -ItemType File -Force | Out-Null
    Write-Output "P28C=REDEPLOYED"
}

Write-Output "LOG_TARGET=$LOG_ROOT (if writable) else game stdout / this-process output"
Write-Output ""
Write-Output "NEXT_STEPS (Dorothy):"
Write-Output "  1. Launch The Sims 4 with WickedWhims + Nevely anim + P28C override + this debug mod."
Write-Output "  2. Open/trigger the Nevely animation list so ordinals' SexAnimationInstance load."
Write-Output "  3. Quit and paste back the trace block (stdout / $LOG_ROOT / whichever showed):"
Write-Output "       HOOK_INSTALLED=..." 
Write-Output "       RAW_ARG=... / INSTANCE_DISPLAY_NAME=... / INSTANCE_NAME=... / LOCALIZED_HASH=..."
Write-Output ""
Write-Output "TO_ROLLBACK:  powershell -ExecutionPolicy Bypass -File .\scripts\ww_p29a_rollback.ps1"
Write-Output "VERDICT=DEPLOYED"
exit 0
