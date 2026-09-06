# ww_p32_catalog_groundtruth.ps1 -- READ-ONLY real-XML ground-truth census that
#   closes the P32 catalogue site-B/C bridge (location forms + gender tokens) and
#   gives the ordinal-318 bridge portrait (site D) from real evidence.
#
# ZERO write to Mods / saves / source.  Writes (only if --Out given):
#   output\p32\p32_catalog_groundtruth.txt
#
# Run (Windows box, real package):
#   powershell -ExecutionPolicy Bypass -File .\scripts\ww_p32_catalog_groundtruth.ps1
#   optional: -Mods <dir> -Py <python.exe> -Source <path.package> -Out <path.txt>
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
function Fail($reason) { Write-Output "VERDICT=FAIL"; Write-Output "REASON=$reason"; exit 1 }

if (-not (Test-Path -LiteralPath $Py)) { $Py = "python" }
if ($Source -ne "") {
    if (-not (Test-Path -LiteralPath $Source)) { Fail "SOURCE_MISSING=$Source" }
    $srcPath = $Source
} else {
    if (-not (Test-Path -LiteralPath $Mods)) { Fail "Mods_MISSING=$Mods" }
    $pkg = Get-ChildItem -LiteralPath $Mods -Recurse -Filter "WW_Nevely42_Animations.package" -File -ErrorAction SilentlyContinue
    if (-not $pkg) { Fail "WW_NEVELY42_PACKAGE_NOT_FOUND under $Mods (pass -Source)" }
    $srcPath = $pkg[0].FullName
}
$EX = Join-Path $WORKSPACE "scripts\ww_p32_catalog_groundtruth.py"
if (-not (Test-Path -LiteralPath $EX)) { Fail "EXTRACTOR_MISSING=$EX" }

Write-Output "SOURCE=$srcPath"
Write-Output "PY=$Py"
$argList = @($srcPath)
if ($Out -ne "") { $argList += @("--out", $Out) }
& $Py $EX @argList
if ($LASTEXITCODE -ne 0) { Fail "GROUNDTRUTH_NONZERO($LASTEXITCODE)" }
Write-Output "P32_CATALOG_GROUNDTRUTH=OK"
exit 0
