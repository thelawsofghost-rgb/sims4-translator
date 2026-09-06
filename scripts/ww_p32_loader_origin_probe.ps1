# ww_p32_loader_origin_probe.ps1 -- P32 Step-6 Phase-1: EXACT loader-origin
#   probe (rev B) + TUNING_DEFAULT_SEMANTICS gate.
#
# Rev-B semantics (2026-09-06 Windows probe collateral):
#   * FIELD ORIGIN is CLOSED: the 8 identity inputs resolve as PROVEN_TUNING x6
#     and PROVEN_TRANSFORM x2 via the EXACT known dataflow (encoded in
#     ww_p32_loader_origin_probe.ORIGIN_ROWS), NEVER via a generic STORE tracer.
#   * The real remaining gate is TUNING_DEFAULT_SEMANTICS: the exact default each
#     correlated tuning key yields when ABSENT must be proven from
#     _ts4_animations_tuning.pyc / TunableFactory / structure definition.
#
# This runner (READ-ONLY):
#   * reads the REAL package's animation XML -> STRUCTURAL carrier census with the
#     REAL tuning keys (animation_x_offset/.../object_*/prop_*/animation_version)
#   * reports origin (closed) + corrected carrier counts (small stats)
#   * if a _ts4_animations_tuning.pyc can be extracted from the .ts4script it is
#     handed to the Windows default extractor; the gate only goes YES when EVERY
#     correlated key's missing-default is bytecode-PROVEN.  Otherwise it FAILS
#     CLOSED with FULL_CORPUS_SAFE_TO_RECONSTRUCT=NO + STOP_REASON (no catalog).
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
Write-Output "--- P32 EXACT loader-origin (origin CLOSED) + TUNING_DEFAULT_SEMANTICS gate ---"

# Locate the real .ts4script (for _ts4_animations_tuning.pyc default evidence).
$tuningPyc = ""
$ts4 = Get-ChildItem -LiteralPath $Mods -Recurse -Filter "*.ts4script" -File -ErrorAction SilentlyContinue
if ($ts4) {
    $tuningMember = $null
    Add-Type -AssemblyName System.IO.Compression.FileSystem | Out-Null
    $zip = [System.IO.Compression.ZipFile]::OpenRead($ts4[0].FullName)
    try {
        foreach ($e in $zip.Entries) {
            if ($e.Name -like "_ts4_animations_tuning.pyc") { $tuningMember = $e; break }
        }
    } finally { $zip.Dispose() }
    if ($tuningMember) {
        $tuningPyc = Join-Path $env:TEMP ("p32_tuning_" + [System.Guid]::NewGuid().ToString("N") + ".pyc")
        $zip = [System.IO.Compression.ZipFile]::OpenRead($ts4[0].FullName)
        try {
            $src = $zip.GetEntry($tuningMember.FullName)
            $outF = [System.IO.File]::Create($tuningPyc)
            try { $src.Open().CopyTo($outF) } finally { $outF.Dispose() }
        } finally { $zip.Dispose() }
        Write-Output "TUNING_PYC=$tuningPyc (member $($tuningMember.FullName))"
    } else {
        Write-Output "TUNING_PYC=NONE (_ts4_animations_tuning.pyc not found in $($ts4[0].Name)); defaults gate will FAIL CLOSED"
    }
}

$argList = @($srcPath, "--out-dir", $OutDir)
if ($tuningPyc -ne "") { $argList += @("--tuning-pyc", $tuningPyc) }
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
