# ============================================================================
# ww_p28b_zero_xml_deploy.ps1 —— P28B 零修改 XML Override Canary (Windows 真机执行)
#
# 目的: 判定游戏内中文失败到底在
#   A. 我们重新封装/构建出来的 WW_ANIM_XML override 本身, 还是
#   B. P27 对 animation_raw_display_name 的修改。
# 部署一个"XML 字节零修改"的 same-TGI override。若部署后动画整组消失
# -> 问题在 DBPF 封装/metadata/压缩兼容 (A); 若正常且仍英文 -> 问题在 XML 内容 (B)。
#
# 阶段1 (只读): 双重独立验证 (P28B report_check + tgi_check, 后者直接与真实源包
#   做 XML 字节比对) + Resource.cfg 审计 (复用可证明的 ww_p28a_cfg_audit.py,
#   --p27-dir P28B_Overrides -> 复用同一 high-priority 决策机制).
# 阶段2 (部署): 备份 Resource.cfg(fail-closed) -> 追加 P28B_Overrides 高优先级规则 ->
#   建 P28B_Overrides 目录并复制 override -> 移除 P27 modified canary(同 TGI,避免共存歧义)
#   -> 删 localthumbcache -> 源包 SHA 前后一致。
#
# 铁律 (fail-closed):
#   * 绝不修改/移动/删除/重命名源包 WW_Nevely42_Animations.package。
#   * 备份 Resource.cfg.p28b_backup, 已存在绝不覆盖。
#   * 只写: $CANARY_PKG(target) + $RESOURCE_CFG(带备份)。
#   * 只删: P27 modified canary ($P27_CANARY_PKG) + $CACHE(localthumbcache)。
#   * 不得删除/触碰任何其他 Mod。
#   * 双重验证 (report_check + tgi_check) 全 PASS 才允许任何写操作。
#   * 本文件 ASCII-only; 中文语义判断全部在 Python 侧 (report_check) 完成。
#
# PS5.1 兼容铁律: 所有 native (python) 调用走 Run-Python (stderr 落盘, 不触发
# NativeCommandError 吞 traceback), 依 $LASTEXITCODE 判成败, 失败打印完整 stderr。
# ============================================================================
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

$TMP_DIR = Join-Path $env:TEMP ("p28b_" + [guid]::NewGuid().ToString("N"))
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
$CFG_BACKUP  = Join-Path $MODS "Resource.cfg.p28b_backup"
$CANARY_DIR  = Join-Path $MODS "P28B_Overrides"
$CANARY_PKG  = Join-Path $CANARY_DIR "WW_Nevely42_ZeroXML_Override.package"
$P27_CANARY_PKG = Join-Path $MODS "P27_Overrides\WW_P27_DisplayName_Override.package"
$OVERRIDE_PKG = "D:\projects\sims4_trans\output\ww_p28b\WW_Nevely42_ZeroXML_Override.package"
$REPORT_TXT   = "D:\projects\sims4_trans\output\ww_p28b\ww_p28b_zero_xml_report.txt"
# 虚拟目标引用: cfg_audit 用其 basename 构建 P28B_Overrides/<name> 虚拟相对路径 (文件可不存在)
$CANARY_REF   = Join-Path $MODS "P28B_Overrides\WW_Nevely42_ZeroXML_Override.package"
$SOURCE_PKG  = Join-Path $MODS "2026.7.20\WW_Nevely42_Animations.package"
$CACHE       = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\localthumbcache.package"

$CFG_AUDIT      = Join-Path $WORKSPACE "scripts\ww_p28a_cfg_audit.py"
$REPORT_CHECK   = Join-Path $WORKSPACE "scripts\ww_p28b_zero_xml_report_check.py"
$TGI_CHECK      = Join-Path $WORKSPACE "scripts\ww_p28b_zero_xml_tgi_check.py"

function Fail($reason) {
    Write-Output ""
    Write-Output "VERDICT=FAIL"
    Write-Output "REASON=$reason"
    Write-Output "READY_FOR_GAME_TEST=NO"
    exit 1
}
function Sha256($p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower() }

Write-Output "=== P28B ZERO-XML CANARY ==="

# ---------- 0. 工具&文件存在 ----------
foreach ($t in @($CFG_AUDIT, $REPORT_CHECK, $TGI_CHECK)) {
    if (-not (Test-Path -LiteralPath $t)) { Fail "missing validator: $t" }
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Fail "python not found" }
foreach ($f in @($OVERRIDE_PKG, $REPORT_TXT, $SOURCE_PKG, $RESOURCE_CFG)) {
    if (-not (Test-Path -LiteralPath $f)) { Fail "preflight file missing: $f" }
}

# ---------- 1. 阶段1 (只读): P28B 双重独立验证 ----------
# 1a. report_check (独立验证 report txt, UTF-8 在 Python 侧)
$r = Run-Python -Script $REPORT_CHECK -PyArgs @($REPORT_TXT)
if ($r[0] -ne 0) { Write-Output "PY_STDERR=$($r[2])"; Fail "REPORT_CHECK_FAIL(exit $($r[0]))" }
Write-Output "P28B_REPORT_CHECK=PASS"

# 1b. tgi_check (独立读取 override + 真实源包, 做 XML 字节比对; 不信任 report)
$r = Run-Python -Script $TGI_CHECK -PyArgs @($OVERRIDE_PKG, $SOURCE_PKG)
if ($r[0] -ne 0) { Write-Output "PY_STDERR=$($r[2])"; Fail "TGI_CHECK_FAIL(exit $($r[0]))" }
Write-Output "P28B_TGI_CHECK=PASS"
Write-Output (($r[1] | Where-Object { $_ -like "XML_BYTE_IDENTICAL=*" }) -join '')
Write-Output (($r[1] | Where-Object { $_ -like "INSTANCE=*0x43F3438A94EDEB2B*" }) -join '')

# ---------- 2. 阶段1 (只读): Resource.cfg 判读 (P28B_Overrides 目录) ----------
Write-Output "--- RESOURCE_CFG AUDIT (read-only, P28B_Overrides) ---"
$aud = Run-Python -Script $CFG_AUDIT -PyArgs @("check", $RESOURCE_CFG, $CANARY_REF, $SOURCE_PKG, "--p27-dir", "P28B_Overrides")
if ($aud[0] -ne 0) { Write-Output "PY_STDERR=$($aud[2])"; Fail "CFG_AUDIT_FAIL(exit $($aud[0]))" }
$aud[1] | ForEach-Object { Write-Output $_ }

# ---------- 3. 阶段2 决策 (propose, P28B_Overrides) ----------
$pr = Run-Python -Script $CFG_AUDIT -PyArgs @("propose", $RESOURCE_CFG, $CANARY_REF, $SOURCE_PKG, "--p27-dir", "P28B_Overrides")
if ($pr[0] -ne 0) { Write-Output "PY_STDERR=$($pr[2])"; Fail "CFG_PROPOSE_FAIL(exit $($pr[0]))" }
$prop = $pr[1]
$appendReq   = ($prop | Where-Object { $_ -like "APPEND_REQUIRED=*" }) -replace "APPEND_REQUIRED=",""
$appendB64   = ($prop | Where-Object { $_ -like "APPEND_LINES=*" })    -replace "APPEND_LINES=",""
$proposedPrio= ($prop | Where-Object { $_ -like "PROPOSED_PRIORITY=*" }) -replace "PROPOSED_PRIORITY=",""
$srcEff      = ($prop | Where-Object { $_ -like "SOURCE_EFFECTIVE_PRIORITY=*" }) -replace "SOURCE_EFFECTIVE_PRIORITY=",""
$prioRel     = ($prop | Where-Object { $_ -like "PRIORITY_RELATION=*" }) -replace "PRIORITY_RELATION=",""

Write-Output "SOURCE_EFFECTIVE_PRIORITY=$srcEff"
if ($prioRel -notlike "OVERRIDE_HIGHER*") { Fail "OVERRIDE_PRIORITY_NOT_HIGHER: $prioRel" }
if (-not $proposedPrio) { Fail "no proposed priority" }
Write-Output "PRIORITY_RELATION=$prioRel"

# ---------- 4. 备份 (不覆盖既有备份) ----------
if (Test-Path -LiteralPath $CFG_BACKUP) {
    Write-Output "RESOURCE_CFG_BACKUP=EXISTS (kept, not overwritten)"
} else {
    Copy-Item -LiteralPath $RESOURCE_CFG -Destination $CFG_BACKUP
    if (-not (Test-Path -LiteralPath $CFG_BACKUP)) { Fail "backup write failed" }
    Write-Output "RESOURCE_CFG_BACKUP=OK"
}
$CFG_SHA_BEFORE = Sha256 $RESOURCE_CFG
Write-Output "RESOURCE_CFG_SHA_BEFORE=$CFG_SHA_BEFORE"

# ---------- 5. 追加 P28B_Overrides 高优先级规则 (仅在需要时) ----------
if ($appendReq -eq "YES") {
    if (-not $appendB64) { Fail "append required but no APPEND_LINES" }
    $linesBytes = [Convert]::FromBase64String($appendB64)
    $linesTxt   = [System.Text.Encoding]::UTF8.GetString($linesBytes)
    if ((Sha256 $RESOURCE_CFG) -ne $CFG_SHA_BEFORE) { Fail "Resource.cfg changed during audit -> abort" }
    [System.IO.File]::AppendAllText($RESOURCE_CFG, $linesTxt, [System.Text.Encoding]::UTF8)
    $after = Get-Content -LiteralPath $RESOURCE_CFG -Raw -Encoding UTF8
    if ($after -notmatch "P28B_Overrides") { Fail "append verification failed" }
    Write-Output "OVERRIDE_PRIORITY=$proposedPrio"
    Write-Output "OVERRIDE_PATH=P28B_Overrides\WW_Nevely42_ZeroXML_Override.package"
} else {
    Write-Output "OVERRIDE_PRIORITY=$proposedPrio (reuse existing dedicated rule)"
    Write-Output "OVERRIDE_PATH=P28B_Overrides\WW_Nevely42_ZeroXML_Override.package (existing rule)"
}

# ---------- 6. 建 P28B_Overrides 目录并复制 override ----------
if (-not (Test-Path -LiteralPath $CANARY_DIR)) { New-Item -ItemType Directory -Path $CANARY_DIR | Out-Null }
Copy-Item -LiteralPath $OVERRIDE_PKG -Destination $CANARY_PKG -Force
if (-not (Test-Path -LiteralPath $CANARY_PKG)) { Fail "canary copy failed" }
$OVR_SHA = Sha256 $OVERRIDE_PKG
$DPL_SHA = Sha256 $CANARY_PKG
if ($OVR_SHA -ne $DPL_SHA) { Fail "canary SHA mismatch" }
Write-Output "OVERRIDE_SHA_MATCH=YES"

# ---------- 7. 移除 P27 modified canary (同 TGI, 避免与零修改 override 共存歧义) ----------
if (Test-Path -LiteralPath $P27_CANARY_PKG) {
    Remove-Item -LiteralPath $P27_CANARY_PKG -Force
    if (Test-Path -LiteralPath $P27_CANARY_PKG) { Fail "P27 canary delete failed" }
    Write-Output "P27_MODIFIED_CANARY=DELETED"
} else {
    Write-Output "P27_MODIFIED_CANARY=NOT_PRESENT"
}

# ---------- 8. 源 WW 包 SHA (部署前) ----------
$SRC_SHA_BEFORE = Sha256 $SOURCE_PKG
Write-Output "SOURCE_WW_SHA_BEFORE=$SRC_SHA_BEFORE"

# ---------- 9. 删缓存 (存在才删) ----------
if (Test-Path -LiteralPath $CACHE) {
    Remove-Item -LiteralPath $CACHE -Force
    if (Test-Path -LiteralPath $CACHE) { Fail "localthumbcache delete failed" }
    Write-Output "LOCALTHUMBCACHE=DELETED"
} else {
    Write-Output "LOCALTHUMBCACHE=NOT_FOUND"
}

# ---------- 10. 源 WW 包 SHA (部署后) ----------
$SRC_SHA_AFTER = Sha256 $SOURCE_PKG
Write-Output "SOURCE_WW_SHA_AFTER=$SRC_SHA_AFTER"
if ($SRC_SHA_AFTER -ne $SRC_SHA_BEFORE) { Fail "SOURCE WW CHANGED" }
Write-Output "SOURCE_UNCHANGED=YES"
Write-Output "READY_FOR_GAME_TEST=YES"
exit 0
