# ww_p29a_liveprobe.ps1 -- P29-A task#1: confirm WHERE SexAnimationInstance lives in
# the CURRENT real WW ts4script (read-only).  Does NOT trust the old P15
# transcription; re-verifies live against whatever WW version is installed now.
#
# Steps (read-only, no build / no game / no Mods write):
#   1. locate the real WW script member under -Mods
#      (TURBODRIVER_WickedWhims_Scripts.ts4script)
#   2. run ww_p29a_live_class_probe.py against it
#   3. print LIVE_CLASS_MODULE / LIVE_CLASS_NAME / LIVE_CLASS_CONFIRMED
#
# Exit: 0 = confirmed YES|NO cleanly; 1 = probe failed; 2 = WW ts4script missing.
# Never writes to Mods and never modifies the WW ts4script.  ASCII-only.
# Run: powershell -ExecutionPolicy Bypass -File .\scripts\ww_p29a_liveprobe.ps1
[CmdletBinding()]
param(
    [string]$Mods = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$WORKSPACE = "D:\projects\sims4_trans"
function Fail($reason) { Write-Output "VERDICT=FAIL"; Write-Output "REASON=$reason"; exit 1 }

if (-not (Test-Path -LiteralPath $Mods)) { Fail "Mods missing: $Mods" }
$WW = Get-ChildItem -LiteralPath $Mods -Recurse -Filter "TURBODRIVER_WickedWhims_Scripts.ts4script" -File -ErrorAction SilentlyContinue
if (-not $WW) { Write-Output "VERDICT=FAIL"; Write-Output "REASON=WW_TS4SCRIPT_NOT_FOUND under $Mods"; exit 2 }
$WWPath = $WW[0].FullName
Write-Output "WW_TS4SCRIPT=$WWPath"

$PROBE = Join-Path $WORKSPACE "scripts\ww_p29a_live_class_probe.py"
if (-not (Test-Path -LiteralPath $PROBE)) { Fail "live_class_probe missing: $PROBE" }

Write-Output "--- LIVE CLASS PROBE (read-only, current WW) ---"
$stderrFile = Join-Path $env:TEMP ("p29a_lp_" + [System.Guid]::NewGuid().ToString("N") + ".log")
try {
    $out = & python $PROBE $WWPath 2> $stderrFile
    $code = $LASTEXITCODE
} finally {
    if (Test-Path -LiteralPath $stderrFile) { Remove-Item -LiteralPath $stderrFile -Force }
}
$out | ForEach-Object { Write-Output $_ }
if ($code -ne 0) { Fail "LIVE_PROBE_NONZERO_EXIT($code)" }
Write-Output "VERDICT=OK"
exit 0
