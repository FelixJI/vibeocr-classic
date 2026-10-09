[CmdletBinding()]
param(
    [string]$Version,
    [string]$ArtifactsDir
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$projectVersion = (Get-Content -LiteralPath (Join-Path $root 'version.txt') -Raw).Trim()
if (-not $Version) {
    $Version = $projectVersion
} else {
    $Version = $Version.TrimStart('v')
}
if ($Version -ne $projectVersion) {
    throw "Release version '$Version' does not match project version '$projectVersion'"
}
if (-not $ArtifactsDir) {
    $ArtifactsDir = $env:AUTOMATION_ARTIFACTS_DIR
}
if (-not $ArtifactsDir) {
    $ArtifactsDir = Join-Path $root 'artifacts'
}
$artifacts = [IO.Path]::GetFullPath($ArtifactsDir)
$build = Join-Path $root 'build/release'
foreach ($path in @($artifacts, $build)) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Recurse -Force
    }
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}
$buildVenv = Join-Path $build 'release-venv'
$pythonVersion = (Get-Content -LiteralPath (Join-Path $root '.python-version') -Raw).Trim()
uv venv --python $pythonVersion $buildVenv
if ($LASTEXITCODE -ne 0) { throw 'release build venv creation failed' }
$buildPython = Join-Path $buildVenv 'Scripts/python.exe'
$buildLock = Join-Path $root 'scripts/requirements-build.lock'
uv pip sync --python $buildPython $buildLock
if ($LASTEXITCODE -ne 0) { throw 'release build lock sync failed' }

# ---------------------------------------------------------------------------
# 融合形态：后端在本仓库内构建；Protocol SDK wheel 按权威摘要校验后安装。
# 哈希来源：vibeocr-protocol v2.9.0 Release 的 SHA256SUMS。
# ---------------------------------------------------------------------------
function Invoke-VerifiedDownload {
    param([string]$Url, [string]$ExpectedSha256, [string]$Destination)
    $client = New-Object System.Net.WebClient
    try {
        $client.DownloadFile($Url, $Destination)
    } finally {
        $client.Dispose()
    }
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [System.IO.File]::OpenRead($Destination)
        try {
            $hash = ([System.BitConverter]::ToString(
                $algorithm.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
        } finally {
            $stream.Dispose()
        }
    } finally {
        $algorithm.Dispose()
    }
    if ($hash -ne $ExpectedSha256.ToLowerInvariant()) {
        throw "download hash mismatch for $Url : $hash"
    }
}
$sdkDir = Join-Path $build 'sdk-wheels'
New-Item -ItemType Directory -Path $sdkDir -Force | Out-Null
$contractsWheel = Join-Path $sdkDir 'vibeocr_runtime_contracts-2.9.0-py3-none-any.whl'
$clientWheel = Join-Path $sdkDir 'vibeocr_runtime_client-2.9.0-py3-none-any.whl'
Invoke-VerifiedDownload -Url 'https://github.com/FelixJI/vibeocr-protocol/releases/download/v2.9.0/vibeocr_runtime_contracts-2.9.0-py3-none-any.whl' `
  -ExpectedSha256 'b567867fd4ac503a0d575ccc689d4d4953968d65bbe008cbc95cb6f26c325013' `
  -Destination $contractsWheel
Invoke-VerifiedDownload -Url 'https://github.com/FelixJI/vibeocr-protocol/releases/download/v2.9.0/vibeocr_runtime_client-2.9.0-py3-none-any.whl' `
  -ExpectedSha256 '4fdc519ee46c0dc5bd11828656cae1128597e4176d0ba50bef469b2c6426d7f8' `
  -Destination $clientWheel
uv pip install --no-deps --python $buildPython $contractsWheel $clientWheel
if ($LASTEXITCODE -ne 0) { throw 'Protocol SDK wheel install failed' }

& $buildPython -m build --wheel --no-isolation `
  (Join-Path $root 'apps/vibeocr-backend/packages/vibeocr-backend') --outdir $build
if ($LASTEXITCODE -ne 0) { throw 'vibeocr-backend wheel build failed' }
$backendWheel = (
    Get-ChildItem $build -Filter 'vibeocr_backend-*.whl' |
      Sort-Object LastWriteTime -Descending | Select-Object -First 1
  ).FullName
if (-not $backendWheel) { throw 'vibeocr-backend wheel not found after build' }
uv pip install --no-deps --python $buildPython $backendWheel
if ($LASTEXITCODE -ne 0) { throw 'vibeocr-backend wheel install failed' }

# 发布身份锁：后端 wheel 哈希写入 component-lock。
$locksDir = Join-Path $build 'workspace-locks'
& $buildPython (Join-Path $root 'scripts/generate_workspace_locks.py') `
  --output-dir $locksDir --backend-wheel $backendWheel `
  --policy (Join-Path $root 'component-policy.json')
if ($LASTEXITCODE -ne 0) { throw 'workspace lock generation failed' }
$lock = Join-Path $locksDir 'component-lock.json'
$frontendProtocolLock = Join-Path $locksDir 'frontend-protocol-lock.json'

# 随包分发的 uv.exe（引擎环境管理）。
# 注意：此脚本可能在 PSModulePath 受限的 CI 环境运行，Utility 模块的
# 下载/哈希/解压 cmdlet 不可依赖，这些操作全部走 .NET API。
$uvVersion = '0.12.22'
$uvSha256 = 'ea1397797a0ca15f63516dd0f49c2dde9776db9be5861cab152ebe8ad199894d'
$uvZip = Join-Path $build 'uv.zip'
$uvExtract = Join-Path $build 'uv'
$uvUrl = "https://github.com/astral-sh/uv/releases/download/$uvVersion/uv-x86_64-pc-windows-msvc.zip"
$webClient = New-Object System.Net.WebClient
$webClient.DownloadFile($uvUrl, $uvZip)
$uvHashAlgorithm = [System.Security.Cryptography.SHA256]::Create()
try {
    $uvStream = [System.IO.File]::OpenRead($uvZip)
    try {
        $uvHashBytes = $uvHashAlgorithm.ComputeHash($uvStream)
    } finally {
        $uvStream.Dispose()
    }
} finally {
    $uvHashAlgorithm.Dispose()
}
$uvActualHash = ([System.BitConverter]::ToString($uvHashBytes)).Replace('-', '').ToLowerInvariant()
if ($uvActualHash -ne $uvSha256) {
    throw "uv.exe archive hash mismatch: $uvActualHash"
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory($uvZip, $uvExtract)
$uvBinary = Join-Path $uvExtract 'uv.exe'
if (-not (Test-Path -LiteralPath $uvBinary -PathType Leaf)) {
    throw 'uv.exe not found in downloaded archive'
}

& $buildPython -m build --wheel --no-isolation `
  (Join-Path $root 'apps/vibeocr-pyside') --outdir $build
if ($LASTEXITCODE -ne 0) { throw 'Classic wheel build failed' }
uv pip install --no-deps --python $buildPython --force-reinstall `
  (Get-ChildItem $build -Filter "vibeocr_classic-$Version-*.whl" | `
    Select-Object -First 1).FullName
if ($LASTEXITCODE -ne 0) { throw 'Classic wheel install failed' }
$dist = Join-Path $build 'dist'
$pyinstallerArgs = @(
    '--noconfirm', '--clean', '--onedir', '--windowed',
    '--name', 'VibeOCR',
    '--icon', (Join-Path $root 'resources/app_icon.ico'),
    '--distpath', $dist,
    '--workpath', (Join-Path $build 'pyinstaller'),
    '--specpath', (Join-Path $build 'spec'),
    '--collect-submodules', 'vibeocr.classic',
    '--collect-submodules', 'vibeocr.backend',
    '--collect-data', 'vibeocr.backend',
    '--collect-submodules', 'vibeocr.runtime_client',
    '--collect-submodules', 'vibeocr.runtime_contracts',
    '--collect-data', 'vibeocr.runtime_contracts',
    '--collect-all', 'velopack',
    '--add-data', "$root/resources;resources",
    '--add-data', "$root/CHANGELOG.md;."
)
$hiddenQtModules = @(
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtNetwork',
    'PySide6.QtOpenGL',
    'PySide6.QtPdf',
    'PySide6.QtPositioning',
    'PySide6.QtPrintSupport',
    'PySide6.QtQuick',
    'PySide6.QtQuickWidgets',
    'PySide6.QtSvg',
    'PySide6.QtUiTools',
    'PySide6.QtWebChannel',
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWidgets'
)
$excludedModules = @(
    'torch', 'torchvision', 'paddle', 'scipy', 'sklearn', 'pandas',
    'transformers', 'tokenizers', 'safetensors', 'hf_xet',
    'PySide6.Qt3DAnimation', 'PySide6.Qt3DCore', 'PySide6.Qt3DExtras',
    'PySide6.Qt3DInput', 'PySide6.Qt3DLogic', 'PySide6.Qt3DRender',
    'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtGraphs',
    'PySide6.QtLocation', 'PySide6.QtMultimedia',
    'PySide6.QtMultimediaWidgets', 'PySide6.QtNfc', 'PySide6.QtQuick3D',
    'PySide6.QtQuickControls2', 'PySide6.QtRemoteObjects', 'PySide6.QtScxml',
    'PySide6.QtSensors', 'PySide6.QtSerialBus', 'PySide6.QtSerialPort',
    'PySide6.QtSpatialAudio', 'PySide6.QtSql', 'PySide6.QtTest',
    'PySide6.QtTextToSpeech', 'PySide6.QtVirtualKeyboard',
    'PySide6.QtWebSockets', 'PySide6.QtXml'
)
foreach ($module in $hiddenQtModules) {
    $pyinstallerArgs += @('--hidden-import', $module)
}
foreach ($module in $excludedModules) {
    $pyinstallerArgs += @('--exclude-module', $module)
}
$pyinstallerArgs += (Join-Path $root 'scripts/classic_release_entry.py')
& $buildPython -m PyInstaller @pyinstallerArgs
if ($LASTEXITCODE -ne 0) { throw 'Classic PyInstaller build failed' }
$product = Join-Path $dist 'VibeOCR'
& $buildPython (Join-Path $root 'scripts/prune_pyside_artifact.py') `
  --product-root $product
if ($LASTEXITCODE -ne 0) { throw 'Classic PySide6 payload pruning failed' }
Copy-Item -LiteralPath (Join-Path $root 'LICENSE') -Destination $product
& $buildPython (Join-Path $root 'scripts/finalize_product_release.py') `
  --product-root $product --frontend classic --frontend-version $Version `
  --source-commit (git -C $root rev-parse HEAD).Trim() `
  --component-lock $lock --frontend-protocol-lock $frontendProtocolLock `
  --backend-wheel $backendWheel --uv-binary $uvBinary
if ($LASTEXITCODE -ne 0) { throw 'Classic product binding failed' }
& $buildPython (Join-Path $root 'scripts/verify_pyside_artifact.py') $product --policy (Join-Path $root 'component-policy.json')
if ($LASTEXITCODE -ne 0) { throw 'Classic artifact verification failed' }
$velopackProduct = Join-Path $build 'velopack-product'
& $buildPython (Join-Path $root 'scripts/prepare_velopack_input.py') `
  $product $velopackProduct
if ($LASTEXITCODE -ne 0) { throw 'Velopack immutable input preparation failed' }
$velopackOutput = Join-Path $build 'velopack'
New-Item -ItemType Directory -Path $velopackOutput -Force | Out-Null
# 版本段落注入 feed 的 NotesMarkdown，客户端"发现新版本"弹窗才能展示更新日志。
$releaseNotes = Join-Path $velopackOutput 'release-notes.md'
& $buildPython (Join-Path $root 'scripts/extract_release_notes.py') `
  --changelog (Join-Path $root 'CHANGELOG.md') --version $Version `
  --output $releaseNotes
if ($LASTEXITCODE -ne 0) { throw 'Release notes extraction failed' }
$deltaPlanFile = Join-Path $build 'velopack-delta-plan.json'
$deltaPrepareArgs = @(
  '--repository', 'FelixJI/vibeocr-classic', '--pack-id', 'VibeOCRClassic',
  '--target-version', $Version, '--output-dir', $velopackOutput,
  '--plan-file', $deltaPlanFile
)
if ($env:AUTOMATION_SOURCE_SHA) {
    # automation/release PR 与合并后的 main CI 运行时目标版本的 tag 尚未创建。
    # 必须用 rev-parse -q --verify 静默探测；禁止对原生命令做 stderr 重定向
    # （如 2>$null）：Windows PowerShell 5.1 会把 stderr 包装成 ErrorRecord，
    # 在 $ErrorActionPreference='Stop' 下直接终止脚本。
    $releaseTagCommit = [string](& git -C $root rev-parse -q --verify "refs/tags/v${Version}^{commit}")
    if ($LASTEXITCODE -eq 0 -and $releaseTagCommit.Trim() -eq $env:AUTOMATION_SOURCE_SHA) {
        $deltaPrepareArgs += '--reproduce-published-delta'
    }
}
& $buildPython (Join-Path $root 'scripts/prepare_velopack_delta.py') @deltaPrepareArgs
if ($LASTEXITCODE -ne 0) { throw 'Velopack delta base preparation failed' }
$deltaPlan = Get-Content -LiteralPath $deltaPlanFile -Raw | ConvertFrom-Json
$deltaMode = [string]$deltaPlan.delta_mode
dnx --yes vpk@1.2.0 -- pack `
  --packId VibeOCRClassic --packVersion $Version --packDir $velopackProduct `
  --mainExe VibeOCR.exe --channel win --runtime win-x64 --delta $deltaMode `
  --noInst `
  --releaseNotes $releaseNotes `
  --packAuthors FelixJI --packTitle VibeOCR `
  --icon (Join-Path $root 'resources/app_icon.ico') `
  --outputDir $velopackOutput
if ($LASTEXITCODE -ne 0) { throw 'Velopack release build failed' }
$normalizeFeedArgs = @(
  '--feed', (Join-Path $velopackOutput 'releases.win.json'),
  '--pack-id', 'VibeOCRClassic', '--target-version', $Version
)
if ($deltaPlan.base_version) {
    $normalizeFeedArgs += @('--expected-base-version', [string]$deltaPlan.base_version)
}
& $buildPython (Join-Path $root 'scripts/normalize_velopack_feed.py') @normalizeFeedArgs
if ($LASTEXITCODE -ne 0) { throw 'Velopack feed normalization failed' }
& $buildPython (Join-Path $root 'scripts/verify_velopack_release.py') `
  $velopackOutput `
  --version $Version
if ($LASTEXITCODE -ne 0) { throw 'Velopack release verification failed' }
$velopackOldOutput = Join-Path $build 'velopack-e2e-old'
New-Item -ItemType Directory -Path $velopackOldOutput -Force | Out-Null
dnx --yes vpk@1.2.0 -- pack `
  --packId VibeOCRClassic --packVersion 0.0.1 --packDir $velopackProduct `
  --mainExe VibeOCR.exe --channel win --runtime win-x64 --delta none `
  --noInst `
  --packAuthors FelixJI --packTitle VibeOCR `
  --icon (Join-Path $root 'resources/app_icon.ico') `
  --outputDir $velopackOldOutput
if ($LASTEXITCODE -ne 0) { throw 'Velopack old-version E2E build failed' }
& $buildPython (Join-Path $root 'scripts/verify_velopack_portable_e2e.py') `
  --old-portable (Join-Path $velopackOldOutput 'VibeOCRClassic-win-Portable.zip') `
  --new-feed $velopackOutput --target-version $Version `
  --require-package-type full `
  --work-dir (Join-Path $build 'velopack-portable-e2e') --timeout 1200
if ($LASTEXITCODE -ne 0) { throw 'Velopack Portable two-version E2E failed' }
if ($deltaPlan.base_package) {
    $deltaOldOutput = Join-Path $build 'velopack-delta-e2e-old'
    New-Item -ItemType Directory -Path $deltaOldOutput -Force | Out-Null
    dnx --yes vpk@1.2.0 -- pack `
      --packId VibeOCRClassic --packVersion ([string]$deltaPlan.base_version) `
      --packDir $velopackProduct --mainExe VibeOCR.exe --channel win `
      --runtime win-x64 --delta none --noInst `
      --packAuthors FelixJI --packTitle VibeOCR `
      --icon (Join-Path $root 'resources/app_icon.ico') `
      --outputDir $deltaOldOutput
    if ($LASTEXITCODE -ne 0) { throw 'Velopack delta E2E old Portable build failed' }
    & $buildPython (Join-Path $root 'scripts/verify_velopack_portable_e2e.py') `
      --old-portable (Join-Path $deltaOldOutput 'VibeOCRClassic-win-Portable.zip') `
      --old-package (Join-Path $velopackOutput ([string]$deltaPlan.base_package)) `
      --new-feed $velopackOutput --target-version $Version `
      --require-package-type delta `
      --work-dir (Join-Path $build 'velopack-portable-delta-e2e') --timeout 1200
    if ($LASTEXITCODE -ne 0) { throw 'Velopack Portable delta E2E failed' }
}
# Portable-only：用户可见交付只有 Portable.zip；NUPKG/feed 服务 Velopack
# 自更新。两次 vpk pack 均以 --noInst 禁止生成多余的 Setup.exe。
foreach ($name in @(
    "VibeOCRClassic-$Version-full.nupkg",
    'releases.win.json'
)) {
    Copy-Item -LiteralPath (Join-Path $velopackOutput $name) -Destination $artifacts
}
$deltaPackage = Join-Path $velopackOutput "VibeOCRClassic-$Version-delta.nupkg"
if (Test-Path -LiteralPath $deltaPackage -PathType Leaf) {
    Copy-Item -LiteralPath $deltaPackage -Destination $artifacts
}
Copy-Item -LiteralPath (Join-Path $velopackOutput 'VibeOCRClassic-win-Portable.zip') `
  -Destination (Join-Path $artifacts "VibeOCRClassic-v$Version-win-x64.zip")
Copy-Item -LiteralPath $lock -Destination (Join-Path $artifacts 'component-lock.json')
Copy-Item -LiteralPath $frontendProtocolLock `
  -Destination (Join-Path $artifacts 'frontend-protocol-lock.json')
& $buildPython (Join-Path $root 'scripts/build_spdx_sbom.py') `
  --artifacts-dir $artifacts `
  --repository-name FelixJI/vibeocr-classic --version $Version
if ($LASTEXITCODE -ne 0) { throw 'SBOM build failed' }
