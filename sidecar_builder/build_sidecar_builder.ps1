# build_sidecar_builder.ps1
# 固化 Windows 可复现构建: git clone/pull 后, 仅凭仓库内容 + NuGet restore 即可编译,
# 无需用户手动提供一长串参数 / 无需装 .NET 4.0 Developer Pack / 无需手动寻找 s4pi。
#
# 固定:
#   Configuration = Debug        (勿改 Release: 上游 s4pi PreBuildEvent 硬编码 bin\Debug)
#   Platform      = AnyCPU
#   SolutionDir   = <repo>\vendor\   (s4pi PreBuildEvent 依赖 $(SolutionDir)s4pi\CreateAssemblyVersion\...)
#   FrameworkPathOverride = 由 NuGet 包自动解析 (见下方说明, 勿手工指定)
#
# FrameworkPathOverride 说明:
#   工程引用 Microsoft.NETFramework.ReferenceAssemblies 1.0.3 (/t:Restore 已恢复),
#   其 net40 包附带的 build targets 会在构建时自动设置
#   TargetFrameworkRootPath=<包内 .NETFramework/v4.0> 并禁用 FrameworkPathOverride
#   (EnableFrameworkPathOverride=false, NoStdLib=true, 显式引用 mscorlib)。
#   因此【不在这里手工硬编码 FrameworkPathOverride】—— 那会是机器相关的 NuGet 缓存绝对路径,
#   违背可复现构建; 且会与包自身的 mscorlib 解析冲突。交由包自动处理即可。
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File build_sidecar_builder.ps1
# 产物: sidecar_builder\bin\Debug\SidecarBuilder.exe

$ErrorActionPreference = 'Stop'

# 以本脚本所在目录为仓库根 (兼容从任意 cwd 调用)
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Csproj   = Join-Path $RepoRoot 'sidecar_builder\SidecarBuilder.csproj'
if (-not (Test-Path $Csproj)) {
    Write-Error "找不到 SidecarBuilder.csproj (应为 $Csproj)"
    exit 1
}

# SolutionDir: 指向仓库内 vendor 根 (带尾部反斜杠), 满足 s4pi PreBuildEvent 的硬编码路径
$SolutionDir = (Join-Path $RepoRoot 'vendor\')   # 隐含尾部反斜杠

Write-Host "== RepoRoot     = $RepoRoot"
Write-Host "== SolutionDir  = $SolutionDir"
Write-Host "== Config/Plat  = Debug / AnyCPU"

# 1) 恢复 NuGet 依赖 (Microsoft.NETFramework.ReferenceAssemblies 1.0.3)
Write-Host "== Restore =="
& msbuild $Csproj /t:Restore /p:Configuration=Debug /p:Platform=AnyCPU /p:SolutionDir="$SolutionDir" /v:m
if ($LASTEXITCODE -ne 0) { Write-Error "restore 失败 (exit $LASTEXITCODE)"; exit $LASTEXITCODE }

# 2) 编译 (Debug; 勿用 Release)
Write-Host "== Build =="
& msbuild $Csproj /p:Configuration=Debug /p:Platform=AnyCPU /p:SolutionDir="$SolutionDir" /v:m
if ($LASTEXITCODE -ne 0) { Write-Error "build 失败 (exit $LASTEXITCODE)"; exit $LASTEXITCODE }

# 3) 校验产物
$Exe = Join-Path $RepoRoot 'sidecar_builder\bin\Debug\SidecarBuilder.exe'
if (Test-Path $Exe) {
    Write-Host ""
    Write-Host "BUILD OK: $Exe"
    Write-Host "下一验证命令:"
    Write-Host "  & '$Exe' -source <SRC> -out out.package -type 0x220557DA -group 0x80000000 -inst 0x... -m KEYHASH:EXPECTED:VALUE"
} else {
    Write-Error "构建成功但未找到产物 $Exe"
    exit 1
}
exit 0
