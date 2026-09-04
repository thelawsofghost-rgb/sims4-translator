# ww_p29a_build_on_win.ps1 -- P29-A: build ww_p29a_debug.ts4script under a LOCAL
# CPython whose pyc MAGIC matches the Sims 4 embedded python.
#
# WHY (corrects earlier wrong assumption):
#   The Sims 4 does NOT ship a runnable python.exe we should invoke -- it embeds a
#   CPython runtime (python*_x64.dll).  A .dll is NOT a compiler: the ONLY way to
#   produce a *.pyc the game will load is to run a real CPython interpreter whose
#   importlib.util.MAGIC_NUMBER equals the game's.  So this script:
#
#     1) READS the real pyc magic from a KNOWN game-loadable .pyc (a member of the
#        live WW .ts4script under Mods -- the same bytecode WW runs) ->
#        TARGET_PYC_MAGIC    (via scripts/ww_p29a_game_py.py magic-from-pyc)
#     2) ENUMERATES locally runnable CPythons + their MAGIC_NUMBER
#        (via ... match --target <magic>)
#     3) Selects one whose LOCAL_PYC_MAGIC == TARGET_PYC_MAGIC and runs
#        scripts/ww_p29a_build_ts4script.py under it.
#     4) Verifies the produced .pyc member magic == TARGET_PYC_MAGIC.
#     If no local compiler matches -> FAIL-CLOSED: report target magic + the
#     exact python major.minor to install.  Never guess / download / install /
#     hard-compile with a wrong-version interpreter.
#
# Params:
#   -Mods        : Mods dir to locate the known-game-loadable WW .ts4script.
#                  Optional; defaults to the canonical Dorothy Mods path.
#   -GamePython  : OPTIONAL explicit LOCAL python.exe to PREFER as the compiler
#                  (an "I know this is the right one -- require its magic to match"
#                   override, keyed into match --prefer).  It is still REQUIRED to
#                   have LOCAL_PYC_MAGIC == TARGET_PYC_MAGIC.
#   env WW_GAME_PYTHON: same meaning as -GamePython (fallback prefer).
#
# Outputs: TARGET_PYC_MAGIC=..., COMPILER_PATH=..., COMPILER_VERSION=...,
#          BUILT_PYC_MAGIC=..., PYC_MAGIC_MATCH=YES|NO, BUILD=PASS|FAIL, REASON,
#          VERDICT=OK|FAIL.  ASCII-only; Run-Python named params; no 2>&1.
[CmdletBinding()]
param(
    [string]$Mods = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods",
    [string]$GamePython = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

$WORKSPACE   = "D:\projects\sims4_trans"
$SRC_MOD     = Join-Path $WORKSPACE "scripts\ww_p29a_mod.py"
$BUILDER     = Join-Path $WORKSPACE "scripts\ww_p29a_build_ts4script.py"
$GAMEPY      = Join-Path $WORKSPACE "scripts\ww_p29a_game_py.py"
$OUT_DIR     = Join-Path $WORKSPACE "dist"
$OUT_TS4     = Join-Path $OUT_DIR "ww_p29a_debug.ts4script"
$FIRST_PY    = "python"   # any local python to drive the two read-only probes
$GATE_PY     = $null       # the local python whose magic == target

function Fail($reason) {
    Write-Output "VERDICT=FAIL"
    Write-Output "REASON=$reason"
    exit 1
}

# Run an arbitrary interpreter; return (code, stdout-lines, stderr-text).
function Run-Py {
    param([string]$Interp, [string]$Script, [string[]]$PyArgs)
    $stderrFile = Join-Path $env:TEMP ("p29a_err_" + [System.Guid]::NewGuid().ToString("N") + ".log")
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $Interp $Script @PyArgs 2> $stderrFile
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    $errText = ""
    if (Test-Path -LiteralPath $stderrFile) {
        $errText = Get-Content -LiteralPath $stderrFile -Raw
        Remove-Item -LiteralPath $stderrFile -Force
    }
    return ,@($code, @($out), $errText)
}

function Read-LineValue {
    param([string[]]$Lines, [string]$Key)
    foreach ($ln in $Lines) {
        if ($ln -like "$Key=*") { return $ln.Substring($Key.Length + 1).Trim() }
    }
    return ""
}

Write-Output "=== P29A BUILD-ON-WIN ==="
if (-not (Test-Path -LiteralPath $SRC_MOD))   { Fail "mod source missing: $SRC_MOD" }
if (-not (Test-Path -LiteralPath $BUILDER))   { Fail "builder missing: $BUILDER" }
if (-not (Test-Path -LiteralPath $GAMEPY))    { Fail "game_py helper missing: $GAMEPY" }

# ---- 0. locate the Mods dir we probe ----
if (-not (Test-Path -LiteralPath $Mods -PathType Container)) { Fail "Mods dir not found: $Mods (pass -Mods <path>)" }
$prefer = ""
if ($GamePython)            { $prefer = $GamePython }
elseif ($env:WW_GAME_PYTHON) { $prefer = $env:WW_GAME_PYTHON }

# ---- 1. read TARGET_PYC_MAGIC from a known game-loadable pyc under Mods ----
Write-Output "--- 1. read game pyc magic from live WW ts4script ---"
$g0 = Run-Py -Interp $FIRST_PY -Script $GAMEPY -PyArgs @("magic-from-pyc", "--locate-mod", $Mods)
if ($g0[0] -ne 0) {
    Write-Output (($g0[1] | Out-String).Trim())
    Fail "NO_KNOWN_LOADABLE_PYC (could not read a game .pyc magic under $Mods)."
}
$targetMagic = Read-LineValue -Lines $g0[1] -Key "TARGET_PYC_MAGIC"
Write-Output "TARGET_PYC_MAGIC=$targetMagic"
Write-Output ((($g0[1] | Where-Object { $_ -like 'TARGET_SRC=*' }) | Out-String).Trim())
if (-not $targetMagic) { Fail "empty TARGET_PYC_MAGIC" }

# ---- 2. find a LOCAL python whose magic == target (or fail-closed) ----
Write-Output "--- 2. select local compiler with matching magic ---"
$mArgs = @("match", "--target", $targetMagic)
if ($prefer) { $mArgs += @("--prefer", $prefer) }
$g1 = Run-Py -Interp $FIRST_PY -Script $GAMEPY -PyArgs $mArgs
if ($g1[0] -ne 0) {
    Write-Output (($g1[1] | Out-String).Trim())
    Write-Output "---"
    Write-Output "REASON=NO_MATCHING_PYTHON_COMPILER"
    Write-Output "GUIDANCE=Install a CPython whose importlib.util.MAGIC_NUMBER equals"
    Write-Output "TARGET_PYC_MAGIC (parent python major.minor shown above from the game"
    Write-Output ".pyc). Sims 4 embeds a CPython 3.7-line runtime; if TARGET_PYC_MAGIC"
    Write-Output "is 0d0d0d0a then a 3.7.x python is required. Re-run passing"
    Write-Output "-GamePython <path-to-python.exe> once installed. No downloads/installs"
    Write-Output "are done by this script."
    Fail "NO_MATCHING_PYTHON_COMPILER"
}
Write-Output (($g1[1] | Where-Object { $_ -like 'MATCH=*' -or $_ -like 'MATCH_PYC_MAGIC=*' }) -join "`n")
$matchLine = Read-LineValue -Lines $g1[1] -Key "MATCH"
# MATCH=<path>\t<ver>\t<abi>
$parts = $matchLine -split "`t"
if ($parts.Length -lt 3) { Fail "malformed MATCH output" }
$GATE_PY = $parts[0]
$compVer = $parts[1]
$compAbi = $parts[2]
Write-Output "COMPILER_PATH=$GATE_PY"
Write-Output "COMPILER_VERSION=$compVer ($compAbi)"

# ---- 3. build with the matched compiler ----
Write-Output "--- 3. build under matched compiler ---"
$r = Run-Py -Interp $GATE_PY -Script $BUILDER -PyArgs @("--src", $SRC_MOD, "--out", $OUT_TS4)
if ($r[0] -ne 0) {
    Write-Output "PY_STDERR=$($r[2])"
    Fail "BUILD_FAIL(exit $($r[0]))"
}
Write-Output (($r[1] | Out-String).Trim())
if (-not (Test-Path -LiteralPath $OUT_TS4)) { Fail "BUILD produced no output" }

# ---- 4. verify built pyc magic == target ----
Write-Output "--- 4. verify member magic ---"
$builtMagic = ""
try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
} catch {}
try {
    $zip = [System.IO.Compression.ZipFile]::OpenRead($OUT_TS4)
    try {
        $entry = $zip.GetEntry("ww_p29a_mod.pyc")
        if ($entry) {
            $ms = New-Object System.IO.MemoryStream
            $s = $entry.Open()
            try { $s.CopyTo($ms) } finally { $s.Dispose() }
            $b = $ms.ToArray()
            if ($b.Length -ge 4) {
                $builtMagic = ("{0:x2}{1:x2}{2:x2}{3:x2}" -f $b[0], $b[1], $b[2], $b[3])
            }
        }
    } finally { $zip.Dispose() }
} catch {}
if (-not $builtMagic) { Fail "could not read built member magic from $OUT_TS4" }
Write-Output "BUILT_PYC_MAGIC=$builtMagic"
if ($builtMagic -ne $targetMagic) {
    Fail "PYC_MAGIC_MISMATCH built=$builtMagic target=$targetMagic"
}
Write-Output "PYC_MAGIC_MATCH=YES"
Write-Output "VERDICT=OK"
Write-Output "OUT=$OUT_TS4"
exit 0
