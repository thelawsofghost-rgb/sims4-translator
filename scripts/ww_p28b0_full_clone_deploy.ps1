# ============================================================================
# ww_p28b0_full_clone_deploy.ps1 —— P28B-0 整包 byte-identical clone 部署 (Windows 真机执行)
#
# 判定实验: 把真实 WW_Nevely42_Animations.package 整个文件字节不变地 clone 到
# Mods\P28B0_Overrides\WW_Nevely42_Animations.package, 并配一个明确高于源包的
# Resource.cfg priority。
#
#   FULL_PACKAGE_CLONE=LOADS_OK    -> Nevely 正常存在 (duplicate/high-priority 整包可行)
#   FULL_PACKAGE_CLONE=BREAKS_LOAD -> Nevely 整组消失 (问题在 duplicate/same-TGI/WW 注册语义)
#
# 完全绕开本项目任何 DBPF writer: clone 由 ww_p28b0_full_clone.py 原样复制产出 (不 parse/不重建)。
#
# 流程 (含【post-write re-audit】硬门禁):
#   阶段1 只读: clone 双重验证 (report_check + entry 普查 tgi_check) + Resource.cfg 只读审计
#   阶段2 决策: cfg_audit propose -> 备份 cfg(独立 .p28b0_backup) -> 追加 P28B0 专用规则
#   阶段3 关键: 追加后【重跑 cfg_audit check】, 必须实际得到
#                   SOURCE_EFFECTIVE_PRIORITY / P28B0_OVERRIDE_EFFECTIVE_PRIORITY /
#                   PRIORITY_RELATION=OVERRIDE_HIGHER  且 override_eff > source_eff
#                (不能只信 propose 的拟议值; 以写入后 cfg 的实际判读为准), 否则 fail-closed 不复制。
#   阶段4 部署: 建 P28B0_Overrides -> 复制 clone (SHA==source/clone) -> 删 localthumbcache
#   阶段5 复核: source WW 包 SHA before==after (绝不被改)
#
# 只能写: $CLONE_PKG (P28B0_Overrides) + $RESOURCE_CFG(带备份)。
# 只删: localthumbcache。不碰源包 / 其它 Mod / P27 / P28A / P28B-1。不 auto 启游戏。
#
# 铁律 (fail-closed): 任一步静态/验证不通过即中止, 绝不跳到复制。
# PS5.1: 所有 native(python) 调 Run-Python (stderr 落盘, EAP local Continue, $LASTEXITCODE)。
# 本文件 ASCII-only (中文语义判断全在 Python 侧)。
# ============================================================================
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

$TMP_DIR = Join-Path $env:TEMP ("p28b0_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $TMP_DIR -Force | Out-Null

function Run-Python {
    param(
        [string]$Script,
        [string[]]$PyArgs
    )
    $stderrFile = Join-Path $TMP_DIR "py_err.txt"
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $stdout = & python $Script @PyArgs 2> $stderrFile
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    $errText = ""
    if (Test-Path -LiteralPath $stderrFile) { $errText = (Get-Content -LiteralPath $stderrFile -Raw -Encoding UTF8) }
    return ,@($code, @($stdout), $errText)
}

# ---------- 路径 (Windows 真机, 全 ASCII) ----------
$WORKSPACE   = "D:\projects\sims4_trans"
$MODS        = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods"
$RESOURCE_CFG = Join-Path $MODS "Resource.cfg"
$CFG_BACKUP  = Join-Path $MODS "Resource.cfg.p28b0_backup"
$CLONE_DIR   = Join-Path $MODS "P28B0_Overrides"
$CLONE_PKG   = Join-Path $CLONE_DIR "WW_Nevely42_Animations.package"
$SOURCE_PKG  = Join-Path $MODS "2026.7.20\WW_Nevely42_Animations.package"
$CACHE       = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\localthumbcache.package"

$ARTIFACT_PKG   = "D:\projects\sims4_trans\output\ww_p28b0\WW_Nevely42_Animations.package"
$REPORT_TXT     = "D:\projects\sims4_trans\output\ww_p28b0\ww_p28b0_clone_report.txt"

$GEN       = Join-Path $WORKSPACE "scripts\ww_p28b0_full_clone.py"
$RCHK      = Join-Path $WORKSPACE "scripts\ww_p28b0_full_clone_report_check.py"
$TCHK      = Join-Path $WORKSPACE "scripts\ww_p28b0_full_clone_tgi_check.py"
$AUDIT     = Join-Path $WORKSPACE "scripts\ww_p28b0_cfg_audit.py"

function Fail($reason) {
    Write-Output ""
    Write-Output "VERDICT=FAIL"
    Write-Output "REASON=$reason"
    Write-Output "FULL_PACKAGE_CLONE=ABORTED"
    exit 1
}
function Sha256($p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower() }

Write-Output "=== P28B-0 FULL PACKAGE BYTE-IDENTICAL CLONE ==="

# ---------- 0. 前置: 脚本 & 文件存在 ----------
foreach ($t in @($RCHK, $TCHK, $AUDIT)) {
    if (-not (Test-Path -LiteralPath $t)) { Fail "missing validator: $t" }
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Fail "python not found" }
foreach ($f in @($SOURCE_PKG, $ARTIFACT_PKG, $REPORT_TXT, $RESOURCE_CFG)) {
    if (-not (Test-Path -LiteralPath $f)) { Fail "preflight file missing: $f" }
}
if (Test-Path -LiteralPath $CLONE_PKG) {
    Write-Output "CLONE_ALREADY_PRESENT=YES (aborting to avoid overwrite; run rollback first then deploy)"
    Write-Output "VERDICT=FAIL"
    Write-Output "REASON=CLONE_ALREADY_PRESENT"
    exit 1
}

# ---------- 1. 只读 clone 双重验证 ----------
# 1a. report_check (独立重读 report, 并对 source/clone 实际文件做独立逐字节比对)
$r = Run-Python -Script $RCHK -PyArgs @($REPORT_TXT)
if ($r[0] -ne 0) { Write-Output "PY_STDERR=$($r[2])"; Fail "CLONE_REPORT_CHECK_FAIL(exit $($r[0]))" }
Write-Output "CLONE_REPORT_CHECK=PASS"
Write-Output (($r[1] | Where-Object { $_ -like "PACKAGE_BYTE_IDENTICAL=*" }) -join '')

# 1b. tgi_check (独立 raw-index 普查 source + artifact clone: 同条目数 / 单 WW_XML / TGI 一致)
$r = Run-Python -Script $TCHK -PyArgs @($SOURCE_PKG, $ARTIFACT_PKG)
if ($r[0] -ne 0) { Write-Output "PY_STDERR=$($r[2])"; Fail "CLONE_TGI_CHECK_FAIL(exit $($r[0]))" }
Write-Output "CLONE_TGI_CHECK=PASS"
Write-Output (($r[1] | Where-Object { $_ -like "ENTRY_COUNT_EQUAL=*" }) -join '')
Write-Output (($r[1] | Where-Object { $_ -like "WW_XML_SOURCE_INSTANCE=*" }) -join '')

# 源 artifact SHA (double-check vs source 由 report_check 已独立验证)
$SRC_SHA = Sha256 $SOURCE_PKG
$ART_SHA = Sha256 $ARTIFACT_PKG
if ($SRC_SHA -ne $ART_SHA) { Fail "artifact SHA != source SHA" }
Write-Output "ARTIFACT_SHA_MATCH_SOURCE=YES"

# ---------- 2. 只读 cfg 审计 + propose ----------
Write-Output "--- RESOURCE_CFG AUDIT + PROPOSE (pre-write, read-only) ---"
$pr = Run-Python -Script $AUDIT -PyArgs @("propose", $RESOURCE_CFG)
if ($pr[0] -ne 0) { Write-Output "PY_STDERR=$($pr[2])"; Fail "CFG_PROPOSE_FAIL(exit $($pr[0]))" }
$prop = $pr[1]
$appendB64   = ($prop | Where-Object { $_ -like "APPEND_LINES=*" })    -replace "APPEND_LINES=",""
$proposedPrio= ($prop | Where-Object { $_ -like "PROPOSED_PRIORITY=*" }) -replace "PROPOSED_PRIORITY=",""
$srcEff      = ($prop | Where-Object { $_ -like "SOURCE_EFFECTIVE_PRIORITY=*" }) -replace "SOURCE_EFFECTIVE_PRIORITY=",""
$prioRel     = ($prop | Where-Object { $_ -like "PRIORITY_RELATION=*" }) -replace "PRIORITY_RELATION=",""
$appendReq   = ($prop | Where-Object { $_ -like "APPEND_REQUIRED=*" }) -replace "APPEND_REQUIRED=",""
Write-Output "SOURCE_EFFECTIVE_PRIORITY=$srcEff"
if ($prioRel -notlike "OVERRIDE_HIGHER*") { Fail "OVERRIDE_PRIORITY_NOT_HIGHER: $prioRel" }
if (-not $proposedPrio) { Fail "no proposed priority" }
Write-Output "PRIORITY_RELATION=$prioRel"

# ---------- 3. 备份 cfg (独立 .p28b0_backup, 存在绝不覆盖) ----------
if (Test-Path -LiteralPath $CFG_BACKUP) {
    Write-Output "RESOURCE_CFG_BACKUP=EXISTS (kept)"
} else {
    Copy-Item -LiteralPath $RESOURCE_CFG -Destination $CFG_BACKUP
    if (-not (Test-Path -LiteralPath $CFG_BACKUP)) { Fail "backup write failed" }
    Write-Output "RESOURCE_CFG_BACKUP=OK"
}
$CFG_SHA_BEFORE = Sha256 $RESOURCE_CFG
Write-Output "RESOURCE_CFG_SHA_BEFORE=$CFG_SHA_BEFORE"

# ---------- 4. 追加 P28B0 专用规则 (仅需时) + 立即重跑 post-write re-audit ----------
if ($appendReq -eq "YES") {
    if (-not $appendB64) { Fail "append required but no APPEND_LINES" }
    $linesBytes = [Convert]::FromBase64String($appendB64)
    $linesTxt   = [System.Text.Encoding]::UTF8.GetString($linesBytes)
    if ((Sha256 $RESOURCE_CFG) -ne $CFG_SHA_BEFORE) { Fail "cfg changed during audit -> abort" }
    [System.IO.File]::AppendAllText($RESOURCE_CFG, $linesTxt, [System.Text.Encoding]::UTF8)
    $after = Get-Content -LiteralPath $RESOURCE_CFG -Raw -Encoding UTF8
    if ($after -notmatch "P28B0_Overrides") { Fail "append verification failed" }
    Write-Output "OVERRIDE_PRIORITY=$proposedPrio (appended)"
    Write-Output "OVERRIDE_PATH=P28B0_Overrides\WW_Nevely42_Animations.package"
} else {
    Write-Output "OVERRIDE_PRIORITY=$proposedPrio (reuse existing dedicated rule)"
}

# ---- post-write re-audit (不能只信 propose; 重读实际写入后的 cfg) ----
Write-Output "--- POST-WRITE RE-AUDIT (cfg actually written) ---"
$pa = Run-Python -Script $AUDIT -PyArgs @("check", $RESOURCE_CFG)
if ($pa[0] -ne 0) { Write-Output "PY_STDERR=$($pa[2])"; Fail "POST_WRITE_REAUDIT_FAIL(exit $($pa[0]))" }
$pread = $pa[1]
$postSrcEff = ($pread | Where-Object { $_ -like "SOURCE_EFFECTIVE_PRIORITY=*" }) -replace "SOURCE_EFFECTIVE_PRIORITY=",""
$postCloneEff= ($pread | Where-Object { $_ -like "P28B0_OVERRIDE_EFFECTIVE_PRIORITY=*" }) -replace "P28B0_OVERRIDE_EFFECTIVE_PRIORITY=",""
$postRel    = ($pread | Where-Object { $_ -like "PRIORITY_RELATION=*" }) -replace "PRIORITY_RELATION=",""

if ($postSrcEff -notmatch '^\d+$')   { Fail "re-audit SOURCE_EFFECTIVE_PRIORITY unresolved: $postSrcEff" }
if ($postCloneEff -notmatch '^\d+$') { Fail "re-audit P28B0_OVERRIDE_EFFECTIVE_PRIORITY unresolved: $postCloneEff" }
if ($postRel -notlike "OVERRIDE_HIGHER*") { Fail "post-write PRIORITY_RELATION not OVERRIDE_HIGHER: $postRel" }
if ([int]$postCloneEff -le [int]$postSrcEff) {
    Fail "post-write override_eff($postCloneEff) <= source_eff($postSrcEff) -> cannot deploy (fail-closed)"
}
Write-Output "SOURCE_EFFECTIVE_PRIORITY=$postSrcEff"
Write-Output "P28B0_OVERRIDE_EFFECTIVE_PRIORITY=$postCloneEff"
Write-Output "PRIORITY_RELATION=$postRel"
Write-Output "POST_WRITE_REAUDIT=PASS (actual override_eff:$postCloneEff > src_eff:$postSrcEff)"

# ---------- 5. 部署: 建目录 + 复制 clone + SHA 核对 ----------
if (-not (Test-Path -LiteralPath $CLONE_DIR)) { New-Item -ItemType Directory -Path $CLONE_DIR | Out-Null }
Copy-Item -LiteralPath $ARTIFACT_PKG -Destination $CLONE_PKG -Force
if (-not (Test-Path -LiteralPath $CLONE_PKG)) { Fail "clone copy failed" }
$DPL_SHA = Sha256 $CLONE_PKG
if ($DPL_SHA -ne $SRC_SHA) { Fail "deployed clone SHA mismatch" }
Write-Output "DEPLOYED_CLONE_SHA_MATCH=YES"

# ---------- 6. 源 WW 包 SHA (部署前) ----------
$SRC_SHA_BEFORE = Sha256 $SOURCE_PKG
Write-Output "SOURCE_WW_SHA_BEFORE=$SRC_SHA_BEFORE"

# ---------- 7. 删缓存 (存在才删) ----------
if (Test-Path -LiteralPath $CACHE) {
    Remove-Item -LiteralPath $CACHE -Force
    if (Test-Path -LiteralPath $CACHE) { Fail "localthumbcache delete failed" }
    Write-Output "LOCALTHUMBCACHE=DELETED"
} else {
    Write-Output "LOCALTHUMBCACHE=NOT_FOUND"
}

# ---------- 8. 源 WW 包 SHA (部署后) ----------
$SRC_SHA_AFTER = Sha256 $SOURCE_PKG
Write-Output "SOURCE_WW_SHA_AFTER=$SRC_SHA_AFTER"
if ($SRC_SHA_AFTER -ne $SRC_SHA_BEFORE) { Fail "SOURCE WW CHANGED" }
Write-Output "SOURCE_UNCHANGED=YES"
Write-Output "READY_FOR_GAME_TEST=YES"
exit 0
