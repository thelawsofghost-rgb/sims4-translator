# ww_p32_identifier_source_fixture.ps1 -- P32: read-only ordinal-318 INDEPENDENT
#   source confirmation + golden offline reconciliation against the REAL
#   WW_Nevely42 animation XML.
#
# Reads the source package READ-ONLY; extracts ordinal-318's display/author/
# category/locations/per-actor gender+clip straight from the tuning XML, then
# (with --golden-check) feeds those source fields through the offline reconstructor
# (adding the runtime-only 0.0 sim-transform positions) and prints GOLDEN=PASS/FAIL
# vs the pinned runtime identifier d0528d3795ca42c60ca5a9eb6bad8658ea74e4b5.
#
# Writes ONLY under --OutDir (default D:\projects\sims4_trans\output\p32):
#     p32_identifier_source_fixture_ord318.txt / .json
#   Never writes to Mods / the WW ts4script / saves.
#
# Run:
#   powershell -ExecutionPolicy Bypass -File .\scripts\ww_p32_identifier_source_fixture.ps1
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

$EX = Join-Path $WORKSPACE "scripts\ww_p32_identifier_source_fixture.py"
if (-not (Test-Path -LiteralPath $EX)) { Fail "EXTRACTOR_MISSING=$EX" }

Write-Output "SOURCE=$srcPath"
Write-Output "PY37=$Py37"
Write-Output "--- P32 ordinal-318 source fixture + golden reconciliation ---"
$argList = @($srcPath, "--out-dir", $OutDir, "--golden-check")
$stderrFile = Join-Path $env:TEMP ("p32_src_" + [System.Guid]::NewGuid().ToString("N") + ".log")
try {
    $out = & $Py37 $EX @argList 2> $stderrFile
    $code = $LASTEXITCODE
} finally {
    if (Test-Path -LiteralPath $stderrFile) { Remove-Item -LiteralPath $stderrFile -Force }
}
$out | ForEach-Object { Write-Output $_ }
if ($code -ne 0) { Fail "NONZERO_EXIT($code) -> GOLDEN or SOURCE_GATE not satisfied (see above)" }

Write-Output "VERDICT=OK"
exit 0
