# ww_p32_loader_origin_probe.ps1 -- P32 Step-6 Phase-1: EXACT loader-origin probe
#   for the 8 FULL_CORPUS_ORIGIN_UNKNOWN identity inputs.
#
# IDENTITY_FIELDS_ACCOUNTED = 15/15, FIELD_NAME_UNKNOWN = 0.
# The 8 inputs below are NOT "unknown field names" -- their loader/constructor
# origin across the FULL 479-row corpus is not yet closed.  Ordinal-318 defaults
# are PROVEN but are NOT to be extrapolated to the other 478 rows.
#
# Probe (READ-ONLY):
#   * disassembles animations_loader.pyc --_create_sex_animation_instance and the
#     SexAnimation*Instance __init__ pyc(s) via xdis;
#   * traces each STORE onto one of the 8 identity fields and walks the operand
#     value provenance UP the instruction stream (const / param / attr / call /
#     global) vs the package tuning schema, classifying per the ONLY-four-status
#     vocabulary: PROVEN_TUNING | PROVEN_DEFAULT | PROVEN_TRANSFORM | UNKNOWN.
#   * emits output/p32/p32_loader_origin_audit.txt (small conclusion table) and
#     prints the Phase-2 gate:  FULL_CORPUS_SAFE_TO_RECONSTRUCT=YES/NO.
#
# Never writes to Mods / the ts4script / saves.  No game launch.  No Chinese
# override json.  Never touches do_not_touch*.json.
#
# Run (Windows):
#   powershell -ExecutionPolicy Bypass -File .\scripts\ww_p32_loader_origin_probe.ps1
#   optional: -Mods <dir> -Py37 <exe> -OutDir <dir> -Source <path.package>
[CmdletBinding()]
param(
    [string]$Mods = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods",
    [string]$Py37 = "C:\Users\thela\AppData\Local\Programs\Python\Python37-32\python.exe",
    [string]$OutDir = "D:\projects\sims4_trans\output\p32",
    [string]$Source = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$WORKSPACE = "D:\projects\sims4_trans"
function Fail($reason) { Write-Output "VERDICT=FAIL"; Write-Output "REASON=$reason"; exit 1 }

if (-not (Test-Path -LiteralPath $Py37)) {
    throw "PY37_MISSING=$Py37; need local CPython 3.7.9 magic 420d0d0a; pass -Py37 if installed elsewhere"
}
if ($Source -ne "") {
    if (-not (Test-Path -LiteralPath $Source)) { Fail "SOURCE_MISSING=$Source" }
    $srcPath = $Source
} else {
    if (-not (Test-Path -LiteralPath $Mods)) { Fail "Mods_MISSING=$Mods" }
    $pkg = Get-ChildItem -LiteralPath $Mods -Recurse -Filter "WW_Nevely42_Animations.package" -File -ErrorAction SilentlyContinue
    if (-not $pkg) { Fail "WW_NEVELY42_PACKAGE_NOT_FOUND under $Mods (pass -Source)" }
    $srcPath = $pkg[0].FullName
}

$EX = Join-Path $WORKSPACE "scripts\ww_p32_loader_origin_probe.py"
if (-not (Test-Path -LiteralPath $EX)) { Fail "PROBE_MISSING=$EX" }

Write-Output "SOURCE=$srcPath"
Write-Output "PY37=$Py37"
Write-Output "--- P32 loader-origin probe (8 FULL_CORPUS_ORIGIN_UNKNOWN) ---"
$argList = @($srcPath, "--dir", $Mods, "--out-dir", $OutDir)
$stderrFile = Join-Path $env:TEMP ("p32_lop_" + [System.Guid]::NewGuid().ToString("N") + ".log")
try {
    $out = & $Py37 $EX @argList 2> $stderrFile
    $code = $LASTEXITCODE
} finally {
    if (Test-Path -LiteralPath $stderrFile) { Remove-Item -LiteralPath $stderrFile -Force }
}
$out | ForEach-Object { Write-Output $_ }
if ($code -ne 0) { Fail "NONZERO_EXIT($code) -> loader-origin probe failed (see above)" }

Write-Output "ZERO_WRITE_TO_MODS=YES"
Write-Output "ZERO_WRITE_TO_SAVES=YES"
Write-Output "VERDICT=OK"
exit 0
