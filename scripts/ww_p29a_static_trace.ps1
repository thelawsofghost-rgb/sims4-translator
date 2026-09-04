# ww_p29a_static_trace.ps1 -- P29-A tasks #2/#3: static DATAFLOW trace of the CURRENT
# WW loader -> SexAnimationInstance constructor, using the REAL local CPython that
# matches the game (3.7.9 / magic 420d0d0a) + NATIVE marshal + stdlib dis.  NO xdis.
#
# Inputs (all read-only): the live WW .ts4script under -Mods.  We trace the
# animations_loader.pyc FIRST (the constructor call site), then animation_instance.
#
# Run: powershell -ExecutionPolicy Bypass -File .\scripts\ww_p29a_static_trace.ps1
#   optional: -Detail  (also disassembles each constructor-call LOAD/CALL window)
#             -Members "a.pyc,b.pyc"  (restrict which members to scan)
#
# Never writes to Mods, never modifies the WW ts4script, never runs game code.
[CmdletBinding()]
param(
    [string]$Mods = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods",
    [string]$Py37 = "C:\Users\thela\AppData\Local\Programs\Python\Python37-32\python.exe",
    [switch]$Detail,
    [string]$Members = ""
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

$TRACER = Join-Path $WORKSPACE "scripts\ww_p29a_static_trace.py"
if (-not (Test-Path -LiteralPath $TRACER)) { Fail "static trace missing: $TRACER" }

Write-Output "WW_TS4SCRIPT=$WWPath"
Write-Output "PY37=$Py37"
Write-Output "--- STATIC DATAFLOW TRACE (native, current WW) ---"
$argsList = @($WWPath)
if ($Detail) { $argsList += "--detail" }
if ($Members) { foreach ($m in $Members.Split(",")) { if ($m) { $argsList += "--member"; $argsList += $m } } }

$stderrFile = Join-Path $env:TEMP ("p29a_st_" + [System.Guid]::NewGuid().ToString("N") + ".log")
try {
    $out = & $Py37 $TRACER @argsList 2> $stderrFile
    $code = $LASTEXITCODE
} finally {
    if (Test-Path -LiteralPath $stderrFile) { Remove-Item -LiteralPath $stderrFile -Force }
}
$out | ForEach-Object { Write-Output $_ }
if ($code -ne 0) { Fail "STATIC_TRACE_NONZERO_EXIT($code)" }
Write-Output "VERDICT=OK"
exit 0
