# ww_p29a_display_source_trace.ps1 -- P29-A: pin the CURRENT-WW bytecode chain
#   animation_raw_display_name -> display_name -> SexAnimationInstance(...)
# inside the REAL loader, with the REAL local CPython 3.7.9 (magic 420d0d0a) +
# NATIVE marshal.  NO xdis.  Read-only (never writes to Mods / WW ts4script / Nevely).
#
# Run:
#   powershell -ExecutionPolicy Bypass -File .\scripts\ww_p29a_display_source_trace.ps1
#   optional: -InstanceMember <path.pyc>  (custom instance member)
#             -LoaderMember  <path.pyc>   (custom loader member)
#             -Func _create_sex_animation_instance
#
# Required (fail-closed, prints the missing path): local Python37-32 3.7.9 whose
# MAGIC_NUMBER == the WW pyc magic (420d0d0a).  If the real python is elsewhere,
# pass -Py37.
[CmdletBinding()]
param(
    [string]$Mods = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods",
    [string]$Py37 = "C:\Users\thela\AppData\Local\Programs\Python\Python37-32\python.exe",
    [string]$LoaderMember = "wickedwhims/sex/animations/animations_loader.pyc",
    [string]$InstanceMember = "wickedwhims/sex/animations/animation_instance.pyc",
    [string]$Func = "_create_sex_animation_instance"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$WORKSPACE = "D:\projects\sims4_trans"
function Fail($reason) { Write-Output "VERDICT=FAIL"; Write-Output "REASON=$reason"; exit 1 }

if (-not (Test-Path -LiteralPath $Mods)) { Fail "Mods_MISSING=$Mods" }
if (-not (Test-Path -LiteralPath $Py37)) {
    throw "PY37_MISSING=$Py37; need local CPython 3.7.9 magic 420d0d0a; pass -Py37 if installed elsewhere"
}

$WW = Get-ChildItem -LiteralPath $Mods -Recurse -Filter "TURBODRIVER_WickedWhims_Scripts.ts4script" -File -ErrorAction SilentlyContinue
if (-not $WW) { Fail "WW_TS4SCRIPT_NOT_FOUND under $Mods" }
$WWPath = $WW[0].FullName

$TRACER = Join-Path $WORKSPACE "scripts\ww_p29a_display_source_trace.py"
if (-not (Test-Path -LiteralPath $TRACER)) { Fail "TRACER_MISSING=$TRACER" }

Write-Output "WW_TS4SCRIPT=$WWPath"
Write-Output "PY37=$Py37"
Write-Output "--- DISPLAY SOURCE BYTECODE TRACE (native marshal, current WW) ---"
$argList = @($WWPath, "--loader", $LoaderMember, "--instance", $InstanceMember,
             "--func", $Func)
$stderrFile = Join-Path $env:TEMP ("p29a_dispst_" + [System.Guid]::NewGuid().ToString("N") + ".log")
try {
    $out = & $Py37 $TRACER @argList 2> $stderrFile
    $code = $LASTEXITCODE
} finally {
    if (Test-Path -LiteralPath $stderrFile) { Remove-Item -LiteralPath $stderrFile -Force }
}
$out | ForEach-Object { Write-Output $_ }
if ($code -ne 0) { Fail "NONZERO_EXIT($code)" }
Write-Output "VERDICT=OK"
exit 0
