# ww_p32_identifier_catalog.ps1 -- P32 Phase-2: offline 479-row identifier catalog
#   over the REAL WW_Nevely42 animation XML.
#
# READ-ONLY, GROUNDED, FAIL-CLOSED:
#   * opens the source package with the authoritative pins (sha cd0093f2..b87d416 /
#     WW_ANIM_XML instance 0x43F3438A94EDEB2B / ENTRY_COUNT must == 479).
#   * per row mirrors the loader -> runtime -> get_identifier bridge (display/author/
#     category, location scalar-or-list-or-empty, actor gender+clip, the row's object
#     slot, its animation_props_list props in order) and feeds the SAME authoritative
#     reconstructor used by the golden ordinal-318 proof -- NO second identity
#     formula.  Required core missing / unmapped gender token -> UNKNOWN w/ exact
#     reason; no guessing / empty-string / drop / demotion.
#   * writes ONLY under --OutDir:
#         p32_identifier_catalog.csv
#         p32_identifier_catalog_report.txt
#     ZERO write to Mods / saves / the WW ts4script / source.
#
# Run (Windows box, real package):
#   powershell -ExecutionPolicy Bypass -File .\scripts\ww_p32_identifier_catalog.ps1
#   optional: -Mods <dir> -Py <python.exe>=/C:\Python314\python.exe -OutDir <dir>
#             -Source <path.package>
[CmdletBinding()]
param(
    [string]$Mods = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods",
    [string]$Py = "C:\Python314\python.exe",
    [string]$OutDir = "D:\projects\sims4_trans\output\p32",
    [string]$Source = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$WORKSPACE = "D:\projects\sims4_trans"
function Fail($reason) { Write-Output "VERDICT=FAIL"; Write-Output "REASON=$reason"; exit 1 }

# Interpreter (F): the catalog is plain 3.7-safe source (it no longer imports the
# canary builder whose PEP-585 annotation `tuple[bool, list[str]]` broke CPython
# 3.7), so ANY python runs it.  Windows + P32 is run on the real package only;
# prefer a modern interpreter (>=3.9) if present, else whichever python is on PATH.
if (-not (Test-Path -LiteralPath $Py)) {
    $Py = "python"
    # confirm the selected interpreter accepts PEP-585-free run by probing version
    $probe = & $Py -c "import sys;print(sys.version_info[0], sys.version_info[1])" 2> $null
    if ($LASTEXITCODE -ne 0) {
        Fail "PY_MISSING: give -Py <python.exe> (>=3.9 preferred; 3.7 OK for catalog)"
    }
    Write-Output "PY_DEFAULT=$($probe)"
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

$csv = Join-Path $OutDir "p32_identifier_catalog.csv"
$rpt = Join-Path $OutDir "p32_identifier_catalog_report.txt"
$haveRpt = (Test-Path -LiteralPath $csv) -and (Test-Path -LiteralPath $rpt)

# Exit-code contract (w/o assuming stdout was captured fully):
#   FAIL/source/XML/golden (no report) -> python exit 3, $haveRpt False.
#   STOP  UNKNOWN>0 artifacts emitted -> python exit 2, $haveRpt True.
#   GO    safe artifacts emitted      -> python exit 0, $haveRpt True.
if ((-not $haveRpt)) {
    Fail "NONZERO_EXIT($code) -- no catalog+report written (source gate / parse / golden fail)"
}

$GO = ($out | Select-String -Pattern "VERDICT=GO" -Quiet)
$GOLD = ($out | Select-String -Pattern "ORD318_GOLDEN=PASS" -Quiet)
if ($GO) {
    Write-Output "P32_IDENTIFIER_CATALOG=GO  (ORD318_GOLDEN=PASS / FULL_CATALOG_SAFE=YES)"
    exit 0
} else {
    Write-Output "P32_IDENTIFIER_CATALOG=STOP (report written; see $rpt)"
    # artifacts exist; operator adjudicates.  exit 0 when golden PASS but UNKNOWN>0,
    # exit 1 only when the golden FAILed (ordinal-318 bridge must re-pass).
    if ($GOLD) { exit 0 } else { exit 1 }
}
