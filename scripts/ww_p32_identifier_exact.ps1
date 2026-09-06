# ww_p32_identifier_exact.ps1 -- P32: STATIC EXACT extractor over the real WW
#   ts4script (read-only, native marshal) to recover the FINAL 5 nested expressions
#   (locations / actor x3 / props) of SexAnimationInstance.get_identifier that the
#   P29-G2 decisive transcription did not expand, plus bodies of any actor/prop
#   method those nested bodies CALL.
#
# Runs under the REAL local CPython 3.7.9 whose MAGIC_NUMBER == WW pyc magic
# (420d0d0a).  Native marshal; NO xdis.  Read-only on the ts4script; extracts the
# code objects and writes ONLY:
#     D:\projects\sims4_trans\output\p32\p32_identifier_exact.txt
#     D:\projects\sims4_trans\output\p32\p32_identifier_exact_callees.txt
#   Never writes to Mods / the WW ts4script / Nevely / saves.
#
# Run:
#   powershell -ExecutionPolicy Bypass -File .\scripts\ww_p32_identifier_exact.ps1
#   optional: -Mods <dir>  -Py37 <exe>  -OutDir <dir>
#
# Fail-closed: if magic mismatch on all candidates or target not found, exits 3.
[CmdletBinding()]
param(
    [string]$Mods = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods",
    [string]$Py37 = "C:\Users\thela\AppData\Local\Programs\Python\Python37-32\python.exe",
    [string]$OutDir = "D:\projects\sims4_trans\output"
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

$EX = Join-Path $WORKSPACE "scripts\ww_p32_identifier_exact.py"
if (-not (Test-Path -LiteralPath $EX)) { Fail "EXTRACTOR_MISSING=$EX" }

Write-Output "WW_TS4SCRIPT=$WWPath"
Write-Output "PY37=$Py37"
Write-Output "--- P32 STATIC EXACT (native marshal, current WW) ---"
$argList = @("--ts4script", $WWPath, "--out-dir", $OutDir)
$stderrFile = Join-Path $env:TEMP ("p32_ie_" + [System.Guid]::NewGuid().ToString("N") + ".log")
try {
    $out = & $Py37 $EX @argList 2> $stderrFile
    $code = $LASTEXITCODE
} finally {
    if (Test-Path -LiteralPath $stderrFile) { Remove-Item -LiteralPath $stderrFile -Force }
}
$out | ForEach-Object { Write-Output $_ }
if ($code -ne 0) { Fail "NONZERO_EXIT($code)" }

$mainR = Join-Path $OutDir "p32\p32_identifier_exact.txt"
$calR  = Join-Path $OutDir "p32\p32_identifier_exact_callees.txt"
if (-not (Test-Path -LiteralPath $mainR)) { Fail "MAIN_ARTIFACT_MISSING=$mainR" }
if (-not (Test-Path -LiteralPath $calR))  { Fail "CALLEE_ARTIFACT_MISSING=$calR" }

$found = Select-String -LiteralPath $mainR -Pattern "TARGET=FOUND:YES" -Quiet
if (-not $found) { Fail "TARGET_NOT_FOUND_IN_DUMP (see $mainR)" }

Write-Output "ARTIFACT_MAIN=$mainR"
Write-Output "ARTIFACT_CALLEES=$calR"
Write-Output "VERDICT=OK"
exit 0
