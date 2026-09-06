# ww_p33_translation_context.ps1 -- P33 Phase-3: translation-CONTEXT dataset
#   over the REAL P32 identifier catalog.
#
# NOT a translation / NOT MT.  Reads the real Windows P32 output
#   output\p32\p32_identifier_catalog.csv  (FINAL WINDOWS GATE = PASS, 479 rows)
# and inherits ordinal / raw_display_name / identifier VERBATIM (never
# recomputes the SHA1 -- it is copied straight from P32).  Adds a per-row
# localisation-context layer: P32 bridge-context passthrough, conservative
# multi-entry series detection on adjacent author+stem+trailing-numeral/Roman
# tokens, prev/next raw names, title-structure aid, translation_status/
# note.  ZERO_WRITE_TO_MODS / ZERO_WRITE_TO_SAVES; writes ONLY under --OutDir:
#       p33_translation_context.csv
#       p33_translation_context.jsonl
#       p33_translation_context_report.txt
# The report's P32_ROWS/CONTEXT_ROWS/IDENTIFIER_MATCH_COUNT/IDENTIFIER_MISMATCH_COUNT/
# ORDINAL_UNIQUE_COUNT gate and VERDICT=GO/STOP are printed to stdout.
#
# Run (Windows box, after P32 produced its CSV):
#   powershell -ExecutionPolicy Bypass -File .\scripts\ww_p33_translation_context.ps1
#   optional: -Csv <p32 CSV> -Py <python.exe> -OutDir <dir>
[CmdletBinding()]
param(
    [string]$Py = "C:\Python314\python.exe",
    [string]$OutDir = "D:\projects\sims4_trans\output\p33",
    [string]$Csv = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$WORKSPACE = "D:\projects\sims4_trans"
function Fail($reason) { Write-Output "VERDICT=FAIL"; Write-Output "REASON=$reason"; exit 1 }

if (-not (Test-Path -LiteralPath $Py)) {
    $Py = "python"
    $probe = & $Py -c "import sys;print(sys.version_info[0], sys.version_info[1])" 2> $null
    if ($LASTEXITCODE -ne 0) { Fail "PY_MISSING: give -Py <python.exe>" }
    Write-Output "PY_DEFAULT=$($probe)"
}

if ($Csv -eq "") {
    $cand = Join-Path $WORKSPACE "output\p32\p32_identifier_catalog.csv"
    if (Test-Path -LiteralPath $cand) { $Csv = $cand } else {
        Fail "P32_CSV_NOT_FOUND (pass -Csv <p32_identifier_catalog.csv>)"
    }
}
if (-not (Test-Path -LiteralPath $Csv)) { Fail "Csv_MISSING=$Csv" }

$EX = Join-Path $WORKSPACE "scripts\ww_p33_translation_context.py"
& $Py $EX $Csv --out-dir $OutDir
exit $LASTEXITCODE
