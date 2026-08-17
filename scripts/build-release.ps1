[CmdletBinding()]
param(
    [string]$Version,
    [string]$ReleaseInput,
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
$defaultInputs = Join-Path $root '.release-input'
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
$policy = Join-Path $root 'component-policy.json'
if ($ReleaseInput) {
    $inputs = (Resolve-Path -LiteralPath $ReleaseInput).Path
} else {
    $inputs = $defaultInputs
    if (Test-Path -LiteralPath $inputs) {
        Remove-Item -LiteralPath $inputs -Recurse -Force
    }
    & $buildPython (Join-Path $root 'scripts/resolve_component_releases.py') `
      --policy $policy --output-root $inputs
    if ($LASTEXITCODE -ne 0) { throw 'compatible component resolution failed' }
    & $buildPython (Join-Path $root 'scripts/verify_component_release_input.py') `
      --release-input $inputs
    if ($LASTEXITCODE -ne 0) { throw 'resolved component verification failed' }
}
$protocol = Join-Path $inputs 'protocol'
$protocolSdk = Join-Path $inputs 'protocol-sdk'
$backend = Join-Path $inputs 'backend'
$lock = Join-Path $inputs 'component-lock.json'
$frontendProtocolLock = Join-Path $inputs 'frontend-protocol-lock.json'
if (-not (Test-Path -LiteralPath $lock -PathType Leaf)) {
    throw 'resolved component-lock.json is required'
}
if (-not (Test-Path -LiteralPath $frontendProtocolLock -PathType Leaf)) {
    throw 'resolved frontend-protocol-lock.json is required'
}
$frontendProtocol = Get-Content -LiteralPath $frontendProtocolLock -Raw |
  ConvertFrom-Json
$protocolSdkVersion = [string]$frontendProtocol.version
function Resolve-ProtocolSdkWheel {
    param([string]$Distribution)
    $matches = @(
        $frontendProtocol.artifacts.PSObject.Properties.Name |
          Where-Object {
              $_ -like "$Distribution-$protocolSdkVersion-*.whl"
          }
    )
    if ($matches.Count -ne 1) {
        throw "frontend Protocol lock must select one $Distribution wheel"
    }
    $wheel = Join-Path $protocolSdk $matches[0]
    if (-not (Test-Path -LiteralPath $wheel -PathType Leaf)) {
        throw "frontend Protocol SDK wheel is missing: $($matches[0])"
    }
    return $wheel
}
$contractsWheel = Resolve-ProtocolSdkWheel 'vibeocr_runtime_contracts'
$clientWheel = Resolve-ProtocolSdkWheel 'vibeocr_runtime_client'
uv pip install --no-deps --python $buildPython $contractsWheel $clientWheel
if ($LASTEXITCODE -ne 0) { throw 'verified frontend SDK wheel install failed' }
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
    'torch', 'torchvision', 'paddle', 'cv2', 'scipy', 'sklearn', 'pandas',
    'pymupdf', 'fitz', 'lxml',
    'transformers', 'onnxruntime', 'tokenizers', 'safetensors', 'hf_xet',
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
  --frontend-protocol-release-dir $protocolSdk `
  --protocol-release-dir $protocol `
  --backend-release-dir $backend
if ($LASTEXITCODE -ne 0) { throw 'Classic product binding failed' }
& $buildPython (Join-Path $root 'scripts/verify_pyside_artifact.py') $product
if ($LASTEXITCODE -ne 0) { throw 'Classic artifact verification failed' }
$velopackOutput = Join-Path $build 'velopack'
New-Item -ItemType Directory -Path $velopackOutput -Force | Out-Null
dnx --yes vpk@1.2.0 -- pack `
  --packId VibeOCRClassic --packVersion $Version --packDir $product `
  --mainExe VibeOCR.exe --channel win --runtime win-x64 --delta none `
  --packAuthors FelixJI --packTitle VibeOCRClassic `
  --icon (Join-Path $root 'resources/app_icon.ico') `
  --outputDir $velopackOutput
if ($LASTEXITCODE -ne 0) { throw 'Velopack release build failed' }
& $buildPython (Join-Path $root 'scripts/verify_velopack_release.py') `
  $velopackOutput `
  --version $Version
if ($LASTEXITCODE -ne 0) { throw 'Velopack release verification failed' }
# Portable-only：用户可见交付只有 Portable.zip；NUPKG/feed 服务 Velopack
# 自更新。vpk 生成的 Setup.exe 留在中间目录，不进入发布资产。
foreach ($name in @(
    "VibeOCRClassic-$Version-full.nupkg",
    'VibeOCRClassic-win-Portable.zip',
    'releases.win.json'
)) {
    Copy-Item -LiteralPath (Join-Path $velopackOutput $name) -Destination $artifacts
}
Copy-Item -LiteralPath $lock -Destination (Join-Path $artifacts 'component-lock.json')
Copy-Item -LiteralPath $frontendProtocolLock `
  -Destination (Join-Path $artifacts 'frontend-protocol-lock.json')
& $buildPython (Join-Path $root 'scripts/build_spdx_sbom.py') `
  --artifacts-dir $artifacts `
  --repository-name FelixJI/vibeocr-classic --version $Version
if ($LASTEXITCODE -ne 0) { throw 'SBOM build failed' }
