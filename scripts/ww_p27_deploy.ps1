# ============================================================================
# ww_p27_deploy.ps1 —— P27 override 一次性安全部署 (Windows 真机执行)
#
# 只负责把已经 PASS 的真机 P27 override 部署到 Sims 4 Mods, 并做完整性复核。
# 绝不修改原始 WW 源包 / 不启动游戏 / 不启动 P24 / 不改脚本 / 不额外清理。
#
# 铁律 (fail-closed):
#   * 任一预检不满足 -> 立即退出 (exit != 0), 不部署。
#   * 只写:  $OVERRIDE_TARGET (ZZZ_WW_P27_DisplayName_Override.package)
#   * 只删:  $LOCALTHUMBCACHE (localthumbcache.package, 存在才删, 不存在跳过)
#   * 绝对不写/改/删/移: 原始 WW 源包 及任何其他 Mod 文件。
#
# 用途: 真机 PowerShell 中:
#   powershell -ExecutionPolicy Bypass -File scripts\ww_p27_deploy.ps1
# ============================================================================
$ErrorActionPreference = "Stop"

# ---- 控制台输出编码: 尽可能让中文正常显示 (不影响内部判断, 不改变报告文件) ----
# PS 5.1 (Windows PowerShell) 终端可能仍乱码, 此处仅尽力; 内部判断基于正确 UTF-8 解码后的字符串.
try {
    # 若可为控制台设置 UTF-8 输出编码则设置; 失败不影响逻辑
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

# ---- 静态可验标记: 指向本文件 report 读取必须显式 UTF-8 ----
# UTF8_READ_MARKER: report 读取唯一入口必须带 -Encoding UTF8 (见第 2 步预检)

# ---------- 路径 (Windows 真机) ----------
$WORKSPACE    = "D:\projects\sims4_trans"
$SOURCE_PKG   = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods\2026.7.20\WW_Nevely42_Animations.package"
$OVERRIDE_PKG = "D:\projects\sims4_trans\output\ww_p27\ww_p27_displayname_override.package"
$REPORT_TXT   = "D:\projects\sims4_trans\output\ww_p27\ww_p27_displayname_override.txt"
$TARGET       = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\Mods\ZZZ_WW_P27_DisplayName_Override.package"
$CACHE        = "C:\Users\thela\Documents\Electronic Arts\The Sims 4\localthumbcache.package"

$EXPECTED_TYPE     = "0x7DF2169C"
$EXPECTED_INSTANCE = "0x43F3438A94EDEB2B"
$WHITEBOX_INSTANCE = "0x4444444400000002"

$TOOL = Join-Path $WORKSPACE "scripts\ww_p27_tgi_check.py"

function Fail($reason) {
    Write-Output ""
    Write-Output "VERDICT=FAIL"
    Write-Output "REASON=$reason"
    Write-Output "READY_FOR_GAME_TEST=NO"
    exit 1
}

function Sha256($p) {
    (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower()
}

Write-Output "=== P27 DEPLOYMENT ==="

# ---------- 0. 必要工具存在 ----------
if (-not (Test-Path -LiteralPath $TOOL)) { Fail "缺少 tgi 机验脚本: $TOOL" }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Fail "找不到 python" }

# ---------- 1. 预检: 文件存在 ----------
foreach ($f in @($SOURCE_PKG, $OVERRIDE_PKG, $REPORT_TXT)) {
    if (-not (Test-Path -LiteralPath $f)) { Fail "预检文件缺失: $f" }
}
Write-Output "PREFLIGHT=CHECKING"

# ---------- 2. 预检: report txt 内容 ----------
# 显式 UTF-8 读取 (Windows PowerShell 默认 Get-Content 用系统代码页读 UTF-8 会产生 mojibake, 必须避开):
$rep = Get-Content -LiteralPath $REPORT_TXT -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
if (-not $rep) { Fail "无法读取 report txt" }
if ($rep -notmatch "VERDICT:\s*PASS")    { Fail "report 无 VERDICT: PASS" }
if ($rep -notmatch "ZERO_WRITE_TO_MODS=YES") { Fail "report 无 ZERO_WRITE_TO_MODS=YES" }
if ($rep -notmatch "type\s*=\s*$($EXPECTED_TYPE)") { Fail "report type 非 $EXPECTED_TYPE" }
if ($rep -notmatch "instance\s*=\s*$($EXPECTED_INSTANCE)") { Fail "report instance 非 $EXPECTED_INSTANCE" }
if ($rep -match $WHITEBOX_INSTANCE) { Fail "report 出现白盒 instance $WHITEBOX_INSTANCE -> 拒绝" }
if ($rep -notmatch "已替换校验:\s*8/8") { Fail "report 无 已替换校验: 8/8 OK" }
# ordinal 299-306 全部为 抓奸 1..抓奸 8
foreach ($i in 1..8) {
    $o = 298 + $i   # 299..306
    if ($rep -notmatch "ordinal\s+$o\s+raw\s*=\s*'抓奸 $i'") { Fail "report ordinal $o 非 '抓奸 $i'" }
}
Write-Output "REPORT_VERDICT=PASS"
Write-Output "TYPE=$EXPECTED_TYPE"
Write-Output "INSTANCE=$EXPECTED_INSTANCE"
Write-Output "TARGETS=8/8"

# ---------- 3. 预检: override 包本体 TGI 机验 (复用现有 parser, 不依赖 txt) ----------
$tgiOut = & python $TOOL 2>&1
$tgiCode = $LASTEXITCODE
if ($tgiCode -ne 0) {
    Fail "override 包 TGI 机验失败 (exit $tgiCode):`n$($tgiOut -join "`n")"
}
Write-Output "OVERRIDE_TGI_CHECK=PASS (count=1 type=$EXPECTED_TYPE instance=$EXPECTED_INSTANCE)"

# ---------- 4. 源包 SHA(部署前) ----------
$SOURCE_SHA_BEFORE = Sha256 $SOURCE_PKG
Write-Output "SOURCE_SHA_BEFORE=$SOURCE_SHA_BEFORE"

# ---------- 5. override SHA ----------
$OVERRIDE_SHA = Sha256 $OVERRIDE_PKG

# ---------- 6. 复制部署 ----------
Copy-Item -LiteralPath $OVERRIDE_PKG -Destination $TARGET -Force
if (-not (Test-Path -LiteralPath $TARGET)) { Fail "部署后目标不存在: $TARGET" }
Write-Output "OVERRIDE_DEPLOYED=$TARGET"

# ---------- 7. 部署后 SHA 复核 ----------
$DEPLOYED_SHA = Sha256 $TARGET
if ($DEPLOYED_SHA -ne $OVERRIDE_SHA) {
    Fail "部署文件 SHA 与源 override 不一致 ($DEPLOYED_SHA vs $OVERRIDE_SHA)"
}
Write-Output "OVERRIDE_SHA=$OVERRIDE_SHA"
Write-Output "DEPLOYED_SHA=$DEPLOYED_SHA"
Write-Output "OVERRIDE_SHA_MATCH=YES"

# ---------- 8. 删缓存 (存在才删) ----------
if (Test-Path -LiteralPath $CACHE) {
    Remove-Item -LiteralPath $CACHE -Force
    if (Test-Path -LiteralPath $CACHE) { Fail "localthumbcache 删除失败" }
    Write-Output "LOCALTHUMBCACHE=DELETED"
} else {
    Write-Output "LOCALTHUMBCACHE=NOT_FOUND"
}

# ---------- 9. 源包 SHA(部署后) —— 必须与部署前一致 ----------
$SOURCE_SHA_AFTER = Sha256 $SOURCE_PKG
Write-Output "SOURCE_SHA_AFTER=$SOURCE_SHA_AFTER"
if ($SOURCE_SHA_AFTER -ne $SOURCE_SHA_BEFORE) {
    Write-Output "VERDICT=FAIL"
    Write-Output "REASON=源包 SHA 改变 (BEFORE=$SOURCE_SHA_BEFORE AFTER=$SOURCE_SHA_AFTER)"
    Write-Output "READY_FOR_GAME_TEST=NO"
    exit 1
}
Write-Output "SOURCE_UNCHANGED=YES"
Write-Output "READY_FOR_GAME_TEST=YES"
exit 0
