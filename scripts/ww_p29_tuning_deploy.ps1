# ww_p29_tuning_deploy.ps1 -- P29-TUNING deploy (Windows real machine, ONE-KEY).
#
# Goal (per user 2026-09-04 18:37): while the P28C ordinal299 raw=TEST299 override
# is LIVE, observe at RUNTIME the SAME animation_tuning object the current WW
# loader uses, so we can decide which of A/B/C/D is true for the Nevely
# "Caught Cheating 1"/TEST299 target:
#   A RAW_ATTR=TEST299  DISPLAY_ATTR=OLD   -> RAW_CHANGED_DISPLAY_DERIVED_OLD
#   B RAW_ATTR=OLD      DISPLAY_ATTR=OLD   -> OVERRIDE_NOT_PRESENT_IN_RUNTIME_TUNING
#   C RAW_ATTR=TEST299  DISPLAY_ATTR=TEST299 + RETURN=TEST299 -> TUNING_AND_INSTANCE_CORRECT
#   D target never seen -> TARGET_TUNING_NOT_OBSERVED
#
# What this script does (and only this):
#   1. Build ww_p29_tuning_debug.ts4script under the GAME-magic-matched python
#      (reuses ww_p29a_build_on_win.ps1 in TUNING mode), pyc magic guaranteed to
#      match the game.
#   2. Gates (fail-closed): ps1 structural check + ww_p29_tuning_static_check.py
#      + ww_p29_tuning_logic_test.py + py37 compat gate.
#   3. Place the debug ts4script into Mods root as ww_p29_tuning_debug.ts4script.
#   4. Auto-deploy the ALREADY-VERIFIED P28C TEST299 override (ww_p28c_deploy.ps1,
#      idempotent; keeps its own Resource.cfg backup + post-write re-audit; sets a
#      P29-TUNING flag so rollback restores base).
#   5. Print next steps + the read-log and rollback commands.
#
# Rollback is ww_p29_tuning_rollback.ps1.  Read log is ww_p29_tuning_read_log.ps1.
# Never touches other mod / source WW / Nevely / P27/P28A/P28B artifacts.
#
# ASCII-only; Run-Python uses named params ($PyArgs); no 2>&1 / no &&.
[CmdletBinding()]
param(
    [string]$GamePython = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

$WORKSPACE      = "D:\projects\sims4_trans"
$MODS           = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods"
$SRC_MOD        = Join-Path $WORKSPACE "scripts\ww_p29_tuning_mod.py"
$STATIC         = Join-Path $WORKSPACE "scripts\ww_p29_tuning_static_check.py"
$PSCHK          = Join-Path $WORKSPACE "scripts\ww_p29a_ps1_static_check.py"
$LOGIC          = Join-Path $WORKSPACE "scripts\ww_p29_tuning_logic_test.py"
$BUILD_WIN      = Join-Path $WORKSPACE "scripts\ww_p29a_build_on_win.ps1"
$DEBUG_TS4      = Join-Path $MODS "ww_p29_tuning_debug.ts4script"
$STAGE          = Join-Path $WORKSPACE "dist\ww_p29_tuning_debug.ts4script"
$FLAG_P28C_DEP  = Join-Path $WORKSPACE "dist\ww_p29_tuning_deployed_p28c.flag"
$P28C_DEPLOY    = Join-Path $WORKSPACE "scripts\ww_p28c_deploy.ps1"

function Fail($reason) { Write-Output "VERDICT=FAIL"; Write-Output "REASON=$reason"; exit 1 }
function Sha256($p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower() }

function Run-Python {
    param([string]$Script, [string[]]$PyArgs)
    $stderrFile = Join-Path $env:TEMP ("p29t_err_" + [System.Guid]::NewGuid().ToString("N") + ".log")
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

Write-Output "=== P29-TUNING DEPLOY ==="

# ---------- 0. preflight ----------
if (-not (Test-Path -LiteralPath $MODS))      { Fail "Mods missing: $MODS" }
if (-not (Test-Path -LiteralPath $SRC_MOD))   { Fail "tuning mod source missing: $SRC_MOD" }
if (-not (Test-Path -LiteralPath $STATIC))    { Fail "tuning static_check missing" }
if (-not (Test-Path -LiteralPath $PSCHK))     { Fail "ps1_static_check missing" }
if (-not (Test-Path -LiteralPath $LOGIC))     { Fail "tuning logic_test missing" }
if (-not (Test-Path -LiteralPath $BUILD_WIN)) { Fail "build_on_win missing" }
if (-not (Test-Path -LiteralPath $P28C_DEPLOY)) { Fail "P28C deploy missing (auto-deploy requires it)" }

# ---------- 1. gates ----------
Write-Output "--- PS1 STRUCTURE GATE ---"
$ps = Run-Python -Script $PSCHK
if ($ps[0] -ne 0) { Write-Output "PY_STDERR=$($ps[2])"; Fail "PS1_STRUCTURE_FAIL(exit $($ps[0]))" }
Write-Output (($ps[1] | Where-Object { $_ -like 'VERDICT=*' }) -join '')
Write-Output "PS1_STRUCTURE=PASS"

Write-Output "--- TUNING STATIC GATE ---"
$s = Run-Python -Script $STATIC
if ($s[0] -ne 0) { Write-Output "PY_STDERR=$($s[2])"; Fail "TUNING_STATIC_FAIL(exit $($s[0]))" }
Write-Output "TUNING_STATIC=PASS"

Write-Output "--- TUNING LOGIC GATE ---"
$l = Run-Python -Script $LOGIC
if ($l[0] -ne 0) { Write-Output "PY_STDERR=$($l[2])"; Fail "TUNING_LOGIC_FAIL(exit $($l[0]))" }
Write-Output "TUNING_LOGIC=PASS"
Write-Output "(py37 real 3.7 compile + api gate runs authoritatively inside BUILD step)"

# ---------- 2. build (magic-matched compiler), TUNING mode ----------
Write-Output "--- BUILD (magic-matched compiler) ---"
$b = @(& $BUILD_WIN -Mods $MODS -GamePython $GamePython -SrcMod $SRC_MOD -OutTs4 $STAGE)
if ($LASTEXITCODE -ne 0) { Write-Output ($b -join "`n"); Fail "BUILD_ON_WIN_FAIL" }
Write-Output (($b | Where-Object { $_ -like 'VERDICT=*' -or $_ -like 'OUT=*' -or $_ -like 'TARGET_PYC_MAGIC=*' -or $_ -like 'COMPILER_PATH=*' -or $_ -like 'BUILT_PYC_MAGIC=*' -or $_ -like 'PYC_MAGIC_MATCH=*' }) -join "`n")
if (-not (Test-Path -LiteralPath $STAGE)) { Fail "stage ts4script not produced" }

# ---------- 3. place debug ts4script ----------
Write-Output "--- PLACE DEBUG TS4 ---"
$preSha = if (Test-Path -LiteralPath $DEBUG_TS4) { Sha256 $DEBUG_TS4 } else { "ABSENT" }
Copy-Item -LiteralPath $STAGE -Destination $DEBUG_TS4 -Force
if (-not (Test-Path -LiteralPath $DEBUG_TS4)) { Fail "copy debug ts4 failed" }
Write-Output "DEBUG_TS4=SHA:$(Sha256 $DEBUG_TS4) (was $preSha)"
Write-Output "DEBUG_TS4_PATH=$DEBUG_TS4"

# ---------- 4. auto-deploy P28C TEST299 override ----------
Write-Output "--- P28C TEST299 AUTO-DEPLOY (already-verified artifact) ---"
& $P28C_DEPLOY
if ($LASTEXITCODE -ne 0) { Fail "P28C auto-deploy failed; run ww_p29_tuning_rollback.ps1 to clean" }
if (Test-Path -LiteralPath $FLAG_P28C_DEP) { Remove-Item -LiteralPath $FLAG_P28C_DEP -Force }
New-Item -Path $FLAG_P28C_DEP -ItemType File -Force | Out-Null
Write-Output "P28C_AUTO_DEPLOY=YES"

Write-Output "LOG_TARGET=%TEMP%\ww_p29_tuning_trace.log (writable) else stdout"
Write-Output ""
Write-Output "NEXT_STEPS (Dorothy):"
Write-Output "  1. Launch The Sims 4 (WW + Nevely anims + P28C override + tuning debug mod)."
Write-Output "  2. Open/trigger the Nevely animation list so ordinal 299's tuning object"
Write-Output "     flows through _create_sex_animation_instance."
Write-Output "  3. Quit, then:"
Write-Output "       powershell -ExecutionPolicy Bypass -File .\scripts\ww_p29_tuning_read_log.ps1"
Write-Output "  expect: RAW_ATTR= / DISPLAY_ATTR= / RETURN_INSTANCE_DISPLAY_NAME= "
Write-Output "          / P29_RESULT=RAW_CHANGED_DISPLAY_DERIVED_OLD | "
Write-Output "          OVERRIDE_NOT_PRESENT_IN_RUNTIME_TUNING | TUNING_AND_INSTANCE_CORRECT | "
Write-Output "          TARGET_TUNING_NOT_OBSERVED"
Write-Output "TO_ROLLBACK:  powershell -ExecutionPolicy Bypass -File .\scripts\ww_p29_tuning_rollback.ps1"
Write-Output "VERDICT=DEPLOYED"
exit 0
