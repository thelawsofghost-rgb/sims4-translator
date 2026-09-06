# ww_p32_sex_gender_exact.ps1 -- READ-ONLY static extraction of the real
#   wickedwhims/sex/enums/sex_gender.pyc from the pinned WW Scripts ts4script, to
#   PROVE (bytecode/structure, never by-name) that get_sex_gender_type_by_name
#   maps the raw tuning token 'BOTH' -> SexGenderType.BOTH.
#
# ZERO write to Mods / saves / archive.  Writes only the report file:
#   output\p32\p32_sex_gender_mapping_exact.txt
#
# Run (Windows box, real WW Scripts present):
#   powershell -ExecutionPolicy Bypass -File .\scripts\ww_p32_sex_gender_exact.ps1
#   optional: -Mods <dir> -Py <python.exe> -Source <TURBODRIVER_WickedWhims_Scripts.ts4script>
[CmdletBinding()]
param(
    [string]$Mods = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods",
    [string]$Py = "C:\Python314\python.exe",
    [string]$Out = "",
    [string]$Source = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$WORKSPACE = "D:\projects\sims4_trans"
$PINNED_SHA = "c1851f6178b6d3e47a40981a38be885840667187fc6ab510a208b89e0e5ff3e7"

function Fail($reason) { Write-Output "VERDICT=FAIL"; Write-Output "REASON=$reason"; exit 1 }

if (-not (Test-Path -LiteralPath $Py)) { $Py = "python" }
$WW = ""
if ($Source -ne "") {
    if (-not (Test-Path -LiteralPath $Source)) { Fail "SOURCE_MISSING=$Source" }
    $WW = (Resolve-Path -LiteralPath $Source).Path
} else {
    if (-not (Test-Path -LiteralPath $Mods)) { Fail "Mods_MISSING=$Mods" }
    $f = Get-ChildItem -LiteralPath $Mods -Recurse -Filter "TURBODRIVER_WickedWhims_Scripts.ts4script" -File -ErrorAction SilentlyContinue
    if (-not $f) { Fail "WW_SCRIPTS_TS4SCRIPT_NOT_FOUND under $Mods (pass -Source)" }
    $WW = $f[0].FullName
}
$EX = Join-Path $WORKSPACE "scripts\ww_p32_sex_gender_exact.py"
if (-not (Test-Path -LiteralPath $EX)) { Fail "EXTRACTOR_MISSING=$EX" }

Write-Output "SOURCE=$WW"
Write-Output "PINNED_SHA=$PINNED_SHA"
$argList = @($WW)
if ($Out -ne "") { $argList += @("--out-dir", (Split-Path -Parent $Out)) }
& $Py $EX @argList
$rc = $LASTEXITCODE
if ($rc -ne 0) {
    # rc 3 = sha mismatch on the real archive is a REAL fail (wrong WW build).
    Fail "SEX_GENDER_EXACT_NONZERO($rc)"
}
Write-Output "P32_SEX_GENDER_EXACT=OK"
exit 0
