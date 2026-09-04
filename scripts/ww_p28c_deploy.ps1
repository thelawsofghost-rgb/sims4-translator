# ww_p28c_deploy.ps1 -- P28C deploy (Windows real machine)
# Copies output/ww_p28c/WW_P28C_TEST299_Override.package -> Mods\P28C_Overrides\
# Uses the real-machine-proven Resource.cfg priority mechanism (higher number = higher priority).
# POST-WRITE RE-AUDIT: after appending the P28C rule, re-run ww_p28c_cfg_audit check and require
#   SOURCE_EFFECTIVE_PRIORITY=500 / P28C_OVERRIDE_EFFECTIVE_PRIORITY=600 / PRIORITY_RELATION=OVERRIDE_HIGHER
#   with override_eff > src_eff, otherwise fail-closed (do NOT copy).
# ASCII-only logic; Run-Python wrapper named params ($PyArgs, never $Args).
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

$MODS          = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods"
$RESOURCE_CFG  = Join-Path $MODS "Resource.cfg"
$CFG_BACKUP    = Join-Path $MODS "Resource.cfg.p28c_backup"
$SRC_SUB       = Join-Path $MODS "2026.7.20"
$SOURCE_PKG    = Join-Path $SRC_SUB "WW_Nevely42_Animations.package"
$OVERRIDE_DIR  = Join-Path $MODS "P28C_Overrides"
$OVERRIDE_PKG  = Join-Path $OVERRIDE_DIR "WW_P28C_TEST299_Override.package"
$WORKSPACE     = "D:\projects\sims4_trans"
$ARTIFACT      = Join-Path $WORKSPACE "output\ww_p28c\WW_P28C_TEST299_Override.package"
$REPORT_TXT    = Join-Path $WORKSPACE "output\ww_p28c\ww_p28c_report.txt"
$CACHE         = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\localthumbcache.package"

$CFG_AUDIT  = Join-Path $WORKSPACE "scripts\ww_p28c_cfg_audit.py"
$RCHK       = Join-Path $WORKSPACE "scripts\ww_p28c_report_check.py"
$TCHK       = Join-Path $WORKSPACE "scripts\ww_p28c_tgi_check.py"

function Fail($reason) { Write-Output "VERDICT=FAIL"; Write-Output "REASON=$reason"; exit 1 }
function Sha256($p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower() }

function Run-Python {
    param([string]$Script, [string[]]$PyArgs)
    $stderrFile = Join-Path $env:TEMP ("p28c_err_" + [System.Guid]::NewGuid().ToString("N") + ".log")
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & python $Script @PyArgs 2> $stderrFile
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    $errText = ""
    if (Test-Path $stderrFile) { $errText = Get-Content -LiteralPath $stderrFile -Raw; Remove-Item -LiteralPath $stderrFile -Force }
    return ,@($code, @($out), $errText)
}

Write-Output "=== P28C DEPLOY ==="

# ---------- 0. preflight ----------
if (-not (Test-Path -LiteralPath $SOURCE_PKG))  { Fail "source WW package missing: $SOURCE_PKG" }
if (-not (Test-Path -LiteralPath $ARTIFACT))    { Fail "P28C artifact missing: $ARTIFACT" }
if (-not (Test-Path -LiteralPath $REPORT_TXT))  { Fail "P28C report missing: $REPORT_TXT" }
if (-not (Test-Path -LiteralPath $RESOURCE_CFG)){ Fail "Resource.cfg missing" }

Write-Output "--- DUAL VALIDATION (report_check + tgi_check) ---"
$r = Run-Python -Script $RCHK -PyArgs @($REPORT_TXT)
if ($r[0] -ne 0) { Write-Output "PY_STDERR=$($r[2])"; Fail "P28C_REPORT_CHECK_FAIL(exit $($r[0]))" }
Write-Output "P28C_REPORT_CHECK=PASS"
Write-Output (($r[1] | Where-Object { $_ -like "MEM_SIZE_INDEP_MATCH=*" }) -join '')
Write-Output (($r[1] | Where-Object { $_ -like "P27_MEM_SIZE_REGRESSION_FIXED=*" }) -join '')

$t = Run-Python -Script $TCHK -PyArgs @($SOURCE_PKG, $ARTIFACT)
if ($t[0] -ne 0) { Write-Output "PY_STDERR=$($t[2])"; Fail "P28C_TGI_CHECK_FAIL(exit $($t[0]))" }
Write-Output "P28C_TGI_CHECK=PASS"
Write-Output (($t[1] | Where-Object { $_ -like "TGI_EQUAL=*" }) -join '')
Write-Output (($t[1] | Where-Object { $_ -like "WW_XML_SOURCE_INSTANCE=*" }) -join '')

# MEM_SIZE_MATCH_NEW_XML + TARGET_299 from the generator report (ASCII keys)
$repText = Get-Content -LiteralPath $REPORT_TXT -Raw -Encoding UTF8
if ($repText -notmatch "MEM_SIZE_MATCH_NEW_XML=YES") { Fail "MEM_SIZE_MATCH_NEW_XML != YES" }
if ($repText -notmatch "TARGET_299=TEST299" -and $repText -notmatch "TARGET_ORDINAL=299" -and $repText -notmatch "TARGET_NEW_RAW=TEST299") { Fail "TARGET_299=TEST299 not present" }
Write-Output "MEM_SIZE_MATCH_NEW_XML=YES"
Write-Output "TARGET_299=TEST299"

# artifact double check vs report-independent (report_check already independently byte-verified mem_size)

# ---------- 1. cfg audit + propose (read-only) ----------
Write-Output "--- RESOURCE_CFG AUDIT + PROPOSE (pre-write, read-only) ---"
$p = Run-Python -Script $CFG_AUDIT -PyArgs @("propose", $RESOURCE_CFG)
if ($p[0] -ne 0) { Write-Output "PY_STDERR=$($p[2])"; Fail "CFG_PROPOSE_FAIL(exit $($p[0]))" }
$prop = $p[1]
$appendB64   = ($prop | Where-Object { $_ -like "APPEND_LINES=*" })    -replace "APPEND_LINES=",""
$proposedPrio= ($prop | Where-Object { $_ -like "PROPOSED_PRIORITY=*" }) -replace "PROPOSED_PRIORITY=",""
$srcEff      = ($prop | Where-Object { $_ -like "SOURCE_EFFECTIVE_PRIORITY=*" }) -replace "SOURCE_EFFECTIVE_PRIORITY=",""
$prioRel     = ($prop | Where-Object { $_ -like "PRIORITY_RELATION=*" }) -replace "PRIORITY_RELATION=",""
$appendReq   = ($prop | Where-Object { $_ -like "APPEND_REQUIRED=*" }) -replace "APPEND_REQUIRED=",""
Write-Output "SOURCE_EFFECTIVE_PRIORITY=$srcEff"
if ($prioRel -notlike "OVERRIDE_HIGHER*") { Fail "OVERRIDE_PRIORITY_NOT_HIGHER: $prioRel" }
if (-not $proposedPrio) { Fail "no proposed priority" }
Write-Output "PRIORITY_RELATION=$prioRel"

# ---------- 2. backup cfg ----------
if (Test-Path -LiteralPath $CFG_BACKUP) {
    Write-Output "RESOURCE_CFG_BACKUP=EXISTS (kept)"
} else {
    Copy-Item -LiteralPath $RESOURCE_CFG -Destination $CFG_BACKUP
    if (-not (Test-Path -LiteralPath $CFG_BACKUP)) { Fail "backup write failed" }
    Write-Output "RESOURCE_CFG_BACKUP=OK"
}
$CFG_SHA_BEFORE = Sha256 $RESOURCE_CFG
Write-Output "RESOURCE_CFG_SHA_BEFORE=$CFG_SHA_BEFORE"

# ---------- 3. append P28C rule + immediate post-write re-audit ----------
if ($appendReq -eq "YES") {
    if (-not $appendB64) { Fail "append required but no APPEND_LINES" }
    $linesBytes = [Convert]::FromBase64String($appendB64)
    $linesTxt   = [System.Text.Encoding]::UTF8.GetString($linesBytes)
    if ((Sha256 $RESOURCE_CFG) -ne $CFG_SHA_BEFORE) { Fail "cfg changed during audit -> abort" }
    [System.IO.File]::AppendAllText($RESOURCE_CFG, $linesTxt, [System.Text.Encoding]::UTF8)
    $after = Get-Content -LiteralPath $RESOURCE_CFG -Raw -Encoding UTF8
    if ($after -notmatch "P28C_Overrides") { Fail "append verification failed" }
    Write-Output "OVERRIDE_PRIORITY=$proposedPrio (appended)"
    Write-Output "OVERRIDE_PATH=P28C_Overrides\WW_P28C_TEST299_Override.package"
} else {
    Write-Output "OVERRIDE_PRIORITY=$proposedPrio (reuse existing dedicated rule)"
}

# ---- POST-WRITE RE-AUDIT (do not trust proposed; re-read actual written cfg) ----
Write-Output "--- POST-WRITE RE-AUDIT ---"
$pa = Run-Python -Script $CFG_AUDIT -PyArgs @("check", $RESOURCE_CFG)
if ($pa[0] -ne 0) { Write-Output "PY_STDERR=$($pa[2])"; Fail "POST_WRITE_REAUDIT_FAIL(exit $($pa[0]))" }
$pread = $pa[1]
$postSrcEff = ($pread | Where-Object { $_ -like "SOURCE_EFFECTIVE_PRIORITY=*" }) -replace "SOURCE_EFFECTIVE_PRIORITY=",""
$postOvrEff = ($pread | Where-Object { $_ -like "P28C_OVERRIDE_EFFECTIVE_PRIORITY=*" }) -replace "P28C_OVERRIDE_EFFECTIVE_PRIORITY=",""
$postRel    = ($pread | Where-Object { $_ -like "PRIORITY_RELATION=*" }) -replace "PRIORITY_RELATION=",""

if ($postSrcEff -notmatch '^\d+$') { Fail "re-audit SOURCE_EFFECTIVE_PRIORITY unresolved: $postSrcEff" }
if ($postOvrEff -notmatch '^\d+$') { Fail "re-audit P28C_OVERRIDE_EFFECTIVE_PRIORITY unresolved: $postOvrEff" }
if ($postRel -notlike "OVERRIDE_HIGHER*") { Fail "post-write PRIORITY_RELATION not OVERRIDE_HIGHER: $postRel" }
if ([int]$postOvrEff -le [int]$postSrcEff) {
    Fail "post-write override_eff($postOvrEff) <= source_eff($postSrcEff) -> cannot deploy (fail-closed)"
}
Write-Output "SOURCE_EFFECTIVE_PRIORITY=$postSrcEff"
Write-Output "P28C_OVERRIDE_EFFECTIVE_PRIORITY=$postOvrEff"
Write-Output "PRIORITY_RELATION=$postRel"
Write-Output "POST_WRITE_REAUDIT=PASS (actual override_eff:$postOvrEff > src_eff:$postSrcEff)"

# ---------- 4. deploy (create P28C_Overrides + copy override + SHA verify) ----------
if (-not (Test-Path -LiteralPath $OVERRIDE_DIR)) { New-Item -ItemType Directory -Path $OVERRIDE_DIR | Out-Null }
Copy-Item -LiteralPath $ARTIFACT -Destination $OVERRIDE_PKG -Force
if (-not (Test-Path -LiteralPath $OVERRIDE_PKG)) { Fail "override copy failed" }
$DPL_SHA = Sha256 $OVERRIDE_PKG
$ART_SHA = Sha256 $ARTIFACT
if ($DPL_SHA -ne $ART_SHA) { Fail "deployed override SHA mismatch" }
Write-Output "DEPLOYED_OVERRIDE_SHA_MATCH=YES"

# ---------- 5. source WW SHA (before) ----------
$SRC_SHA_BEFORE = Sha256 $SOURCE_PKG
Write-Output "SOURCE_WW_SHA_BEFORE=$SRC_SHA_BEFORE"

# ---------- 6. delete localthumbcache (if present) ----------
if (Test-Path -LiteralPath $CACHE) {
    Remove-Item -LiteralPath $CACHE -Force
    if (Test-Path -LiteralPath $CACHE) { Fail "localthumbcache delete failed" }
    Write-Output "LOCALTHUMBCACHE=DELETED"
} else {
    Write-Output "LOCALTHUMBCACHE=NOT_FOUND"
}

# ---------- 7. source WW SHA (after) ----------
$SRC_SHA_AFTER = Sha256 $SOURCE_PKG
Write-Output "SOURCE_WW_SHA_AFTER=$SRC_SHA_AFTER"
if ($SRC_SHA_AFTER -ne $SRC_SHA_BEFORE) { Fail "SOURCE WW CHANGED" }
Write-Output "SOURCE_UNCHANGED=YES"
Write-Output "READY_FOR_GAME_TEST=YES"
exit 0
