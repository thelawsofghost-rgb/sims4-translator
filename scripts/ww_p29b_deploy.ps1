# ww_p29b_deploy.ps1 -- P29-B deploy (Windows real machine, ONE-KEY).
#
# GOAL (2026-09-04 21:04): P28C TEST299 already REACHES runtime (instance
# display_name=TEST299); the open question moved DOWNSTREAM to the UI.  This deploy
# installs ww_p29b_display_trace.ts4script, a RUNTIME OBSERVATION mod that hooks the
# REAL UI-facing class methods
#     SexAnimationInstance.get_display_name(self, string_hash, original)
#     SexAnimationInstance.get_picker_row(self, ...)
# by rebinding them transparently (orig(self, *args, **kwargs) -- self + args unchanged,
# never authored) and records, for target instances only (AUTHOR Nevely42 / display_name
# 'TEST299' / 'Caught Cheating 1'), the fields that answer "where does the old English
# reappear": BASE_DISPLAY_NAME / DISPLAY_NAME_OVERRIDE / ORIGINAL_INSTANCE_PRESENT /
# ORIGINAL_INSTANCE_DISPLAY_NAME / ARG_STRING_HASH / ARG_ORIGINAL /
# GET_DISPLAY_NAME_RETURN and the picker fields PICKER_ROW_TEXT/NAME/DESCRIPTION.
# Named root causes emitted inline: UI_USING_ORIGINAL_INSTANCE /
# DISPLAY_NAME_OVERRIDE_WINS / GET_DISPLAY_NAME_IS_SWITCH; picker-stage
# PICKER_ROW_USES_OTHER_SOURCE.  Run-level A/B/C/PICKER_POSTPROCESSING derived
# post-session by ww_p29b_report_check.py (HOOK_ERROR always wins -> INVALID_HOOK_ERROR).
#
# What this script does (and only this):
#   1. Gates: ww_p29a_ps1_static_check + ww_p29b_static_check + ww_p29b_logic_test
#      (py37 real run is authoritative inside the build step).
#   2. Build ww_p29b_display_trace.ts4script under the game-magic python (reuses
#      ww_p29a_build_on_win.ps1 in P29-B mode).
#   3. Place it in Mods as ww_p29b_display_trace.ts4script.
#   4. Auto-deploy the already-verified P28C TEST299 override + set a P29-B flag.
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
$SRC_MOD        = Join-Path $WORKSPACE "scripts\ww_p29b_display_trace.py"
$STA            = Join-Path $WORKSPACE "scripts\ww_p29b_static_check.py"
$PSCHK          = Join-Path $WORKSPACE "scripts\ww_p29a_ps1_static_check.py"
$LOG            = Join-Path $WORKSPACE "scripts\ww_p29b_logic_test.py"
$BUILD_WIN      = Join-Path $WORKSPACE "scripts\ww_p29a_build_on_win.ps1"
$DEBUG_TS4      = Join-Path $MODS "ww_p29b_display_trace.ts4script"
$STAGE          = Join-Path $WORKSPACE "dist\ww_p29b_display_trace.ts4script"
$FLAG_P28C_DEP  = Join-Path $WORKSPACE "dist\ww_p29b_deployed_p28c.flag"
$P28C_DEPLOY    = Join-Path $WORKSPACE "scripts\ww_p28c_deploy.ps1"

function Fail($reason) { Write-Output "VERDICT=FAIL"; Write-Output "REASON=$reason"; exit 1 }
function Sha256($p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower() }

function Run-Python {
    param([string]$Script, [string[]]$PyArgs)
    $stderrFile = Join-Path $env:TEMP ("p29b_err_" + [System.Guid]::NewGuid().ToString("N") + ".log")
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

Write-Output "=== P29-B DEPLOY (UI final display / picker trace) ==="

# ---------- 0. preflight ----------
if (-not (Test-Path -LiteralPath $MODS))      { Fail "Mods missing: $MODS" }
if (-not (Test-Path -LiteralPath $SRC_MOD))   { Fail "p29b mod missing: $SRC_MOD" }
if (-not (Test-Path -LiteralPath $STA))       { Fail "p29b static_check missing" }
if (-not (Test-Path -LiteralPath $PSCHK))     { Fail "ps1_static_check missing" }
if (-not (Test-Path -LiteralPath $LOG))       { Fail "p29b logic_test missing" }
if (-not (Test-Path -LiteralPath $BUILD_WIN)) { Fail "build_on_win missing" }
if (-not (Test-Path -LiteralPath $P28C_DEPLOY)) { Fail "P28C deploy missing (auto-deploy) " }

# ---------- 1. gates ----------
Write-Output "--- PS1 STRUCTURE GATE ---"
$ps = Run-Python -Script $PSCHK
if ($ps[0] -ne 0) { Write-Output "PY_STDERR=$($ps[2])"; Fail "PS1_STRUCTURE_FAIL(exit $($ps[0]))" }
Write-Output "PS1_STRUCTURE=PASS"

Write-Output "--- P29B STATIC GATE ---"
$s = Run-Python -Script $STA
if ($s[0] -ne 0) { Write-Output "PY_STDERR=$($s[2])"; Fail "P29B_STATIC_FAIL(exit $($s[0]))" }
Write-Output "P29B_STATIC=PASS"

Write-Output "--- P29B LOGIC GATE ---"
$l = Run-Python -Script $LOG
if ($l[0] -ne 0) { Write-Output "PY_STDERR=$($l[2])"; Fail "P29B_LOGIC_FAIL(exit $($l[0]))" }
Write-Output "P29B_LOGIC=PASS"
Write-Output "(py37 real compile runs authoritatively inside BUILD step)"

# ---------- 2. build (magic-matched compiler), P29-B mode ----------
Write-Output "--- BUILD (magic-matched compiler) ---"
$b = @(& $BUILD_WIN -Mods $MODS -GamePython $GamePython -SrcMod $SRC_MOD -OutTs4 $STAGE)
if ($LASTEXITCODE -ne 0) { Write-Output ($b -join "`n"); Fail "BUILD_ON_WIN_FAIL" }
Write-Output (($b | Where-Object { $_ -like 'VERDICT=*' -or $_ -like 'OUT=*' -or $_ -like 'TARGET_PYC_MAGIC=*' -or $_ -like 'PYC_MAGIC_MATCH=*' -or $_ -like 'BUILT_PYC_MAGIC=*' -or $_ -like 'COMPILER_PATH=*' }) -join "`n")
if (-not (Test-Path -LiteralPath $STAGE)) { Fail "stage ts4script not produced" }

# ---------- 3. place debug ts4script ----------
Write-Output "--- PLACE DEBUG TS4 ---"
$preSha = if (Test-Path -LiteralPath $DEBUG_TS4) { Sha256 $DEBUG_TS4 } else { "ABSENT" }
Copy-Item -LiteralPath $STAGE -Destination $DEBUG_TS4 -Force
Write-Output "DEBUG_TS4=SHA:$(Sha256 $DEBUG_TS4) (was $preSha)"
Write-Output "DEBUG_TS4_PATH=$DEBUG_TS4"

# ---------- 4. auto-deploy P28C TEST299 override ----------
Write-Output "--- P28C TEST299 AUTO-DEPLOY (artifact already verified) ---"
& $P28C_DEPLOY
if ($LASTEXITCODE -ne 0) { Fail "P28C auto-deploy failed; run ww_p29b_rollback.ps1 to clean" }
if (Test-Path -LiteralPath $FLAG_P28C_DEP) { Remove-Item -LiteralPath $FLAG_P28C_DEP -Force }
New-Item -Path $FLAG_P28C_DEP -ItemType File -Force | Out-Null
Write-Output "P28C_AUTO_DEPLOY=YES"

Write-Output "LOG_TARGET=%TEMP%\ww_p29b_display_trace.log"
Write-Output ""
Write-Output "NEXT_STEPS (Dorothy):"
Write-Output "  1. Launch TS4 (WW + Nevely anims + P28C + p29b display trace)."
Write-Output "  2. Open the Nevely animation picker for the previous target so its row is"
Write-Output "     rendered (this drives SexAnimationInstance.get_picker_row -> get_display_name)."
Write-Output "  3. Quit, then:"
Write-Output "       powershell -ExecutionPolicy Bypass -File .\scripts\ww_p29b_read_log.ps1"
Write-Output "  expect: BASE_DISPLAY_NAME / DISPLAY_NAME_OVERRIDE / ORIGINAL_INSTANCE_PRESENT /"
Write-Output "          ORIGINAL_INSTANCE_DISPLAY_NAME / GET_DISPLAY_NAME_RETURN / PICKER_ROW_TEXT"
Write-Output "          P29B_RESULT=UI_USING_ORIGINAL_INSTANCE | DISPLAY_NAME_OVERRIDE_WINS |"
Write-Output "                      GET_DISPLAY_NAME_IS_SWITCH | PICKER_ROW_USES_OTHER_SOURCE |"
Write-Output "                      PICKER_POSTPROCESSING_OR_OTHER_UI_SOURCE | INVALID_HOOK_ERROR"
Write-Output "TO_ROLLBACK: powershell -ExecutionPolicy Bypass -File .\scripts\ww_p29b_rollback.ps1"
Write-Output "VERDICT=DEPLOYED"
exit 0
