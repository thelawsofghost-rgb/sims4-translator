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
#             -WW_TS4Script <exact wickedwhims archive path>
[CmdletBinding()]
param(
    [string]$Mods = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods",
    [string]$Py37 = "C:\Users\thela\AppData\Local\Programs\Python\Python37-32\python.exe",
    [string]$OutDir = "D:\projects\sims4_trans\output\p32",
    [string]$Source = "",
    [string]$WW_TS4Script = ""
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

$TUNING_MEMBER = "wickedwhims/sex/animations/_ts4_animations_tuning.pyc"  # WW tuning member (exact)

# ---- REQUIRED real WW archive: NO glob / newest / first-*.ts4script guessing ----
# The default WW archive is the one true, only archive that owns WW tuning.
# NEVER auto-pick ww_p29c_display_caller_trace.ts4script or any other ts4script:
# those are test/sidecar scripts and do not carry wickedwhims/sex/animations
# tuning.  -WW_TS4Script overrides if it lives elsewhere.
if ($WW_TS4Script -eq "") {
    $WW_TS4Script = Join-Path $Mods "WickedWhimsMod\TURBODRIVER_WickedWhims_Scripts.ts4script"
}
if (-not (Test-Path -LiteralPath $WW_TS4Script)) { Fail "WW_TS4SCRIPT_NOT_FOUND=$WW_TS4Script" }

Write-Output "WW_TS4SCRIPT=$WW_TS4Script"
# SHA-256 of the exact archive (ASCII-safe .NET hash).
Add-Type -AssemblyName System.Security.Cryptography | Out-Null
$shaBytes = [System.Security.Cryptography.SHA256]::Create().ComputeHash(
    [System.IO.File]::OpenRead($WW_TS4Script))
$sb = New-Object System.Text.StringBuilder
foreach ($b in $shaBytes) { [void]$sb.Append($b.ToString("x2")) }
Write-Output ("WW_TS4SCRIPT_SHA256=" + $sb.ToString())
Write-Output "TUNING_MEMBER=$TUNING_MEMBER"

Write-Output "SOURCE=$srcPath"
Write-Output "PY37=$Py37"
Write-Output "--- P32 EXACT loader-origin (origin CLOSED) + TUNING_DEFAULT_SEMANTICS gate ---"

# Extract tuning member by EXACT archive path; missing -> FATAL stop (no gate NO as
# if it were a semantic conclusion).  CPython 3.7.9 decodes the real .pyc later.
$tuningPyc = ""
Add-Type -AssemblyName System.IO.Compression.FileSystem | Out-Null
$memberFound = $false
$zip = [System.IO.Compression.ZipFile]::OpenRead($WW_TS4Script)
try {
    foreach ($e in $zip.Entries) {
        if ($e.FullName -eq $TUNING_MEMBER) { $memberFound = $true; break }
    }
} finally { $zip.Dispose() }

if ($memberFound) {
    $tuningPyc = Join-Path $env:TEMP ("p32_tuning_" + [System.Guid]::NewGuid().ToString("N") + ".pyc")
    $zip = [System.IO.Compression.ZipFile]::OpenRead($WW_TS4Script)
    try {
        $src = $zip.GetEntry($TUNING_MEMBER)
        $outF = [System.IO.File]::Create($tuningPyc)
        try { $src.Open().CopyTo($outF) } finally { $outF.Dispose() }
    } finally { $zip.Dispose() }
    Write-Output "TUNING_PYC=$tuningPyc (extracted from WW archive)"
} else {
    Write-Output "FATAL=TUNING_MEMBER_NOT_FOUND (member $TUNING_MEMBER absent from $WW_TS4Script)"
    Fail "TUNING_MEMBER_NOT_FOUND"
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
