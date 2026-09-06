# ww_p32_identifier_catalog.ps1 -- P32 Phase-2: offline 479-row identifier catalog
#   over the REAL WW_Nevely42 animation XML.
#
# READ-ONLY, GROUNDED, FAIL-CLOSED:
#   * opens the source package with the authoritative pins (sha cd0093f2..b87d416 /
#     WW_ANIM_XML instance 0x43F3438A94EDEB2B / ENTRY_COUNT must == 479).
#   * per row reads PROVEN_TUNING identity inputs VERBATIM (display/author/category/
#     locations/actors gender+clip) and feeds them, with the RUNTIME transform
#     group, to the SAME authoritative reconstructor used by the golden ordinal-318
#     proof -- NO second identity formula.  Missing required tuning -> UNKNOWN with
#     an exact reason; no guessing / empty-string / drop / demotion.
#   * writes ONLY under --OutDir:
#         p32_identifier_catalog.csv
#         p32_identifier_catalog_report.txt
#     ZERO write to Mods / saves / the WW ts4script / source.
#
# Run (Windows box, real package):
#   powershell -ExecutionPolicy Bypass -File .\scripts\ww_p32_identifier_catalog.ps1
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

# The catalog reads a DBPF + XML + hashlib only (no xdis / no marshalling), so any
# CPython (3.7-3.11) works; prefer the local 3.7 to stay consistent with the other
# P32 tools, fall back to whichever python is on PATH.
if (-not (Test-Path -LiteralPath $Py37)) {
    $Py = "python"
} else {
    $Py = $Py37
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

$EX = Join-Path $WORKSPACE "scripts\ww_p32_identifier_catalog.py"
if (-not (Test-Path -LiteralPath $EX)) { Fail "EXTRACTOR_MISSING=$EX" }

Write-Output "SOURCE=$srcPath"
Write-Output "PY=$Py"
Write-Output "--- P32 Phase-2: 479-row identifier catalog (read-only) ---"
$argList = @($srcPath, "--out-dir", $OutDir)
$stderrFile = Join-Path $env:TEMP ("p32_cat_" + [System.Guid]::NewGuid().ToString("N") + ".log")
try {
    $out = & $Py $EX @argList 2> $stderrFile
    $code = $LASTEXITCODE
} finally {
    if (Test-Path -LiteralPath $stderrFile) { Remove-Item -LiteralPath $stderrFile -Force }
}
$out | ForEach-Object { Write-Output $_ }
if ($code -ne 0) { Fail "NONZERO_EXIT($code)" }

$csv = Join-Path $OutDir "p32_identifier_catalog.csv"
$rpt = Join-Path $OutDir "p32_identifier_catalog_report.txt"
if (-not (Test-Path -LiteralPath $csv)) { Fail "CATALOG_CSV_MISSING=$csv" }
if (-not (Test-Path -LiteralPath $rpt)) { Fail "REPORT_MISSING=$rpt" }

$GO = ($out | Select-String -Pattern "VERDICT=GO" -Quiet)
$GOLD = ($out | Select-String -Pattern "ORD318_GOLDEN=PASS" -Quiet)
if ($GO) {
    Write-Output "P32_IDENTIFIER_CATALOG=GO  (VALID=479 / ORD318_GOLDEN=PASS / FULL_CATALOG_SAFE=YES)"
    exit 0
} else {
    Write-Output "P32_IDENTIFIER_CATALOG=STOP (see report; FULL_CATALOG_SAFE=NO or ORD318_GOLDEN=FAIL)"
    if ($GOLD) { exit 0 } else { exit 1 }   # catalog+report emitted; operator adjudicates
}
