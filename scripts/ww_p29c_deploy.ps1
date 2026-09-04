# ww_p29c_deploy.ps1 -- P29-C deploy (Windows real machine, ONE-KEY).
#
# GOAL (2026-09-04): find WHERE the value that leaves get_display_name() goes, once
# P28C already changed the runtime instance's display_name to 'TEST300' for the
# 'Caught Cheating 2' row.  Prior phases have already shown "evidence" that the value
# is 'TEST300' at many layers (tuning + instance display_name + get_display_name
# RETURN) yet the visible UI still shows 'Caught Cheating 2'.  So XML/STBL/tuning/
# constructor/original_instance are all excluded as the UI switch point.
#
# This deploy installs ww_p29c_display_caller_trace.ts4script: a RUNTIME
# observation mod that hooks ONLY
#     SexAnimationInstance.get_display_name(self, string_hash, original)
# transparently (orig(self, *args, **kwargs), return unmodified, never author)
# and, ONLY when self.display_name == 'TEST300', records the real CALLER CHAIN
# (up to 5 frames) + the nearest 3 caller frames' filtered locals + the exact
# returned object.  It does NOT dump details for the thousands of other animations.
#
# Read the result with ww_p29c_read_log.ps1 which prints a short summary and exports
# the FULL trace to p29c_target_trace.txt for pasting back to ChatGPT.
#
# What this script does (and only this):
#   1. Gates: ww_p29a_ps1_static_check + ww_p29c_static_check + ww_p29c_logic_test
#      (py37 real run is authoritative inside the build step).
#   2. Build ww_p29c_display_caller_trace.ts4script under the game-magic python (reuses
#      ww_p29a_build_on_win.ps1 in P29-C mode) and inspect its layout vs the working
#      P29-TUNING package (LAYOUT_EQUIVALENT=YES expected).
#   3. Place it in Mods as ww_p29c_display_caller_trace.ts4script.
#   4. Auto-deploy the already-verified P28C TEST300 override + set a P29-C flag.
# ASCII-only; Run-Python named params; no 2>&1 / no &&.
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
$SRC_MOD        = Join-Path $WORKSPACE "scripts\ww_p29c_display_caller_trace.py"
$STA            = Join-Path $WORKSPACE "scripts\ww_p29c_static_check.py"
$PSCHK          = Join-Path $WORKSPACE "scripts\ww_p29a_ps1_static_check.py"
$LOG            = Join-Path $WORKSPACE "scripts\ww_p29c_logic_test.py"
$BUILD_WIN      = Join-Path $WORKSPACE "scripts\ww_p29a_build_on_win.ps1"
$INSPECT        = Join-Path $WORKSPACE "scripts\ww_p29b_inspect.py"
$DEBUG_TS4      = Join-Path $MODS "ww_p29c_display_caller_trace.ts4script"
$STAGE          = Join-Path $WORKSPACE "dist\ww_p29c_display_caller_trace.ts4script"
$FLAG_P28C_DEP  = Join-Path $WORKSPACE "dist\ww_p29c_deployed_p28c.flag"
$P28C_DEPLOY    = Join-Path $WORKSPACE "scripts\ww_p28c_deploy.ps1"

function Fail($reason) { Write-Output "VERDICT=FAIL"; Write-Output "REASON=$reason"; exit 1 }
function Sha256($p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower() }

function Run-Python {
    param([string]$Script, [string[]]$PyArgs)
    $stderrFile = Join-Path $env:TEMP ("p29c_err_" + [System.Guid]::NewGuid().ToString("N") + ".log")
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

Write-Output "=== P29-C DEPLOY (UI final display / picker trace) ==="

# ---------- 0. preflight ----------
if (-not (Test-Path -LiteralPath $MODS))      { Fail "Mods missing: $MODS" }
if (-not (Test-Path -LiteralPath $SRC_MOD))   { Fail "p29c mod missing: $SRC_MOD" }
if (-not (Test-Path -LiteralPath $STA))       { Fail "p29c static_check missing" }
if (-not (Test-Path -LiteralPath $PSCHK))     { Fail "ps1_static_check missing" }
if (-not (Test-Path -LiteralPath $LOG))       { Fail "p29c logic_test missing" }
if (-not (Test-Path -LiteralPath $BUILD_WIN)) { Fail "build_on_win missing" }
if (-not (Test-Path -LiteralPath $INSPECT))   { Fail "p29c_inspect missing" }
if (-not (Test-Path -LiteralPath $P28C_DEPLOY)) { Fail "P28C deploy missing (auto-deploy) " }

# ---------- 1. gates ----------
Write-Output "--- PS1 STRUCTURE GATE ---"
$ps = Run-Python -Script $PSCHK
if ($ps[0] -ne 0) { Write-Output "PY_STDERR=$($ps[2])"; Fail "PS1_STRUCTURE_FAIL(exit $($ps[0]))" }
Write-Output "PS1_STRUCTURE=PASS"

Write-Output "--- P29C STATIC GATE ---"
$s = Run-Python -Script $STA
if ($s[0] -ne 0) { Write-Output "PY_STDERR=$($s[2])"; Fail "P29C_STATIC_FAIL(exit $($s[0]))" }
Write-Output "P29C_STATIC=PASS"

Write-Output "--- P29C LOGIC GATE ---"
$l = Run-Python -Script $LOG
if ($l[0] -ne 0) { Write-Output "PY_STDERR=$($l[2])"; Fail "P29C_LOGIC_FAIL(exit $($l[0]))" }
Write-Output "P29C_LOGIC=PASS"
Write-Output "(py37 real compile runs authoritatively inside BUILD step)"

# ---------- 2. build (magic-matched compiler), P29-C mode ----------
Write-Output "--- BUILD (magic-matched compiler) ---"
$b = @(& $BUILD_WIN -Mods $MODS -GamePython $GamePython -SrcMod $SRC_MOD -OutTs4 $STAGE)
if ($LASTEXITCODE -ne 0) { Write-Output ($b -join "`n"); Fail "BUILD_ON_WIN_FAIL" }
Write-Output (($b | Where-Object { $_ -like 'VERDICT=*' -or $_ -like 'OUT=*' -or $_ -like 'TARGET_PYC_MAGIC=*' -or $_ -like 'PYC_MAGIC_MATCH=*' -or $_ -like 'BUILT_PYC_MAGIC=*' -or $_ -like 'COMPILER_PATH=*' }) -join "`n")
if (-not (Test-Path -LiteralPath $STAGE)) { Fail "stage ts4script not produced" }

# ---------- 2b. build-artifact inspection (prove auto-loadable layout, not guess) ----------
Write-Output "--- ARTIFACT INSPECTION (layout vs working P29-TUNING) ---"
$insp = Run-Python -Script $INSPECT -PyArgs @($STAGE)
foreach ($row in $insp[1]) { Write-Output $row }
if ($insp[0] -ne 0) { Fail "INSPECT_FAIL(exit $($insp[0]))" }

# ---------- 3. place debug ts4script ----------
Write-Output "--- PLACE DEBUG TS4 ---"
$preSha = if (Test-Path -LiteralPath $DEBUG_TS4) { Sha256 $DEBUG_TS4 } else { "ABSENT" }
Copy-Item -LiteralPath $STAGE -Destination $DEBUG_TS4 -Force
Write-Output "DEBUG_TS4=SHA:$(Sha256 $DEBUG_TS4) (was $preSha)"
Write-Output "DEBUG_TS4_PATH=$DEBUG_TS4"

# ---------- 4. auto-deploy P28C TEST300 override ----------
Write-Output "--- P28C TEST300 AUTO-DEPLOY (artifact already verified) ---"
& $P28C_DEPLOY
if ($LASTEXITCODE -ne 0) { Fail "P28C auto-deploy failed; run ww_p29c_rollback.ps1 to clean" }
if (Test-Path -LiteralPath $FLAG_P28C_DEP) { Remove-Item -LiteralPath $FLAG_P28C_DEP -Force }
New-Item -Path $FLAG_P28C_DEP -ItemType File -Force | Out-Null
Write-Output "P28C_AUTO_DEPLOY=YES"

Write-Output "LOG_TARGET=%TEMP%\ww_p29c_display_caller_trace.log"
Write-Output ""
Write-Output "NEXT_STEPS (Dorothy):"
Write-Output "  1. Launch TS4 (WW + Nevely anims + P28C + p29c caller trace)."
Write-Output "  2. Open the Nevely animation picker so the 'Caught Cheating 2' ('TEST300')"
Write-Output "     row is rendered (drives SexAnimationInstance.get_display_name on the target)."
Write-Output "  3. Quit, then:"
Write-Output "       powershell -ExecutionPolicy Bypass -File .\scripts\ww_p29c_read_log.ps1"
Write-Output "  expect summary: P29C_MODULE_IMPORTED / HOOK_INSTALLED / TARGET_CALL_COUNT /"
Write-Output "          TARGET_TRACE_FOUND / P29C_RESULT, and the FULL target caller trace"
Write-Output "          exported to p29c_target_trace.txt (staged next to this repo)."
Write-Output "  P29C_RESULT=TARGET_CALLER_TRACE_CAPTURED |"
Write-Output "             TARGET_GET_DISPLAY_NAME_NOT_CALLED |"
Write-Output "             HOOK_NOT_INSTALLED | MODULE_NOT_IMPORTED"
Write-Output "TO_ROLLBACK: powershell -ExecutionPolicy Bypass -File .\scripts\ww_p29c_rollback.ps1"
Write-Output "VERDICT=DEPLOYED"
exit 0
