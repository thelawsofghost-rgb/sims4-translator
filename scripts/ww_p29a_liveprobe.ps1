# ww_p29a_liveprobe.ps1 -- P29-A task#1 (rewrite): LIVE constructor-contract probe of
# the CURRENT WW SexAnimationInstance, using the REAL local CPython that matches the
# game (3.7.9 / magic 420d0d0a) + NATIVE marshal.loads.  NO xdis.
#
# The old P15 transcription (self, animation_id, animation_raw_display_name,
# animation_type) is STALE for current WW; the real __init__ now uses
# (self, animation_id, display_name, ...).  This probe reports the LIVE contract
# without judging against the stale one, and distinguishes:
#     LIVE_CLASS_PRESENT=YES   (the class + its __init__ exist now)
#     EXPECTED_SIGNATURE_MATCH=NO  (informational: not the old shape)
#
# Read-only: never writes to Mods, never modifies the WW ts4script.
# Run: powershell -ExecutionPolicy Bypass -File .\scripts\ww_p29a_liveprobe.ps1
[CmdletBinding()]
param(
    [string]$Mods = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods",
    [string]$Py37 = "C:\Users\thela\AppData\Local\Programs\Python\Python37-32\python.exe",
    [string]$Member = "wickedwhims/sex/animations/animation_instance.pyc",
    [switch]$ListMembers
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$WORKSPACE = "D:\projects\sims4_trans"
function Fail($reason) { Write-Output "VERDICT=FAIL"; Write-Output "REASON=$reason"; exit 1 }

if (-not (Test-Path -LiteralPath $Mods)) { Fail "Mods missing: $Mods" }
if (-not (Test-Path -LiteralPath $Py37)) { Fail "3.7 python missing: $Py37 (expected 3.7.9 / magic 420d0d0a)" }

$WW = Get-ChildItem -LiteralPath $Mods -Recurse -Filter "TURBODRIVER_WickedWhims_Scripts.ts4script" -File -ErrorAction SilentlyContinue
if (-not $WW) { Fail "WW_TS4SCRIPT_NOT_FOUND under $Mods" }
$WWPath = $WW[0].FullName
Write-Output "WW_TS4SCRIPT=$WWPath"
Write-Output "PY37=$Py37"

$PROBE = Join-Path $WORKSPACE "scripts\ww_p29a_live_probe.py"
if (-not (Test-Path -LiteralPath $PROBE)) { Fail "native probe missing: $PROBE" }

Write-Output "--- LIVE CLASS PROBE (native marshal, current WW) ---"
$stderrFile = Join-Path $env:TEMP ("p29a_lp_" + [System.Guid]::NewGuid().ToString("N") + ".log")
try {
    $out = & $Py37 $PROBE $WWPath --member $Member 2> $stderrFile
    $code = $LASTEXITCODE
} finally {
    if (Test-Path -LiteralPath $stderrFile) { Remove-Item -LiteralPath $stderrFile -Force }
}
$out | ForEach-Object { Write-Output $_ }
if ($code -eq 3) { Write-Output "VERDICT=FAIL"; Write-Output "REASON=MAGIC_MISMATCH $Py37 does not match WW pyc; install the 3.7.9 matching 420d0d0a"; exit 3 }
if ($code -ne 0) { Fail "LIVE_PROBE_NONZERO_EXIT($code)" }
Write-Output "VERDICT=OK"
exit 0
