[CmdletBinding()]
param(
    [string]$Version
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$projectFile = Join-Path $root 'apps/vibeocr-pyside/pyproject.toml'
$projectVersion = (
    python -c "import pathlib,tomllib; print(tomllib.loads(pathlib.Path(r'$projectFile').read_text(encoding='utf-8'))['project']['version'])"
).Trim()
if (-not $Version) {
    $Version = $projectVersion
} else {
    $Version = $Version.TrimStart('v')
}
if ($Version -ne $projectVersion) {
    throw "Release version '$Version' does not match project version '$projectVersion'"
}
$artifacts = Join-Path $root 'artifacts'
$build = Join-Path $root '.release-build'
$inputs = Join-Path $root '.release-input'
foreach ($path in @($artifacts, $build, $inputs)) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Recurse -Force
    }
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}
$protocol = Join-Path $inputs 'protocol'
$backend = Join-Path $inputs 'backend'
New-Item -ItemType Directory -Path $protocol, $backend -Force | Out-Null
$lock = Join-Path $root 'component-lock.json'
if (-not (Test-Path -LiteralPath $lock -PathType Leaf)) {
    throw 'component-lock.json is required'
}
$componentLock = Get-Content -LiteralPath $lock -Raw | ConvertFrom-Json
$protocolVersion = [string]$componentLock.protocol.version
$protocolRepository = [string]$componentLock.protocol.repository
$backendVersion = [string]$componentLock.backend.version
$backendRepository = [string]$componentLock.backend.repository
gh release download "v$protocolVersion" --repo $protocolRepository --dir $protocol
if ($LASTEXITCODE -ne 0) { throw 'Protocol release download failed' }
gh release download "v$backendVersion" --repo $backendRepository --dir $backend
if ($LASTEXITCODE -ne 0) { throw 'Backend release download failed' }
foreach ($item in @(
    @{ path = $protocol; repo = $protocolRepository },
    @{ path = $backend; repo = $backendRepository }
)) {
    Get-ChildItem -LiteralPath $item.path -File |
      Where-Object Name -ne 'SHA256SUMS' |
      ForEach-Object {
        gh attestation verify $_.FullName --repo $item.repo
        if ($LASTEXITCODE -ne 0) { throw "attestation failed: $($_.Name)" }
      }
}
python -m pip install build==1.5.0 hatchling==1.27.0 pyinstaller==6.21.0
python -m pip install `
  (Get-ChildItem $protocol -Filter "vibeocr_runtime_contracts-$protocolVersion-*.whl" | Select-Object -First 1).FullName `
  (Get-ChildItem $protocol -Filter "vibeocr_runtime_client-$protocolVersion-*.whl" | Select-Object -First 1).FullName `
  (Get-ChildItem $backend -Filter "vibeocr_backend-$backendVersion-*.whl" | Select-Object -First 1).FullName
if ($LASTEXITCODE -ne 0) { throw 'verified upstream wheel install failed' }
python -m build --wheel --no-isolation (Join-Path $root 'apps/vibeocr-pyside') --outdir $build
if ($LASTEXITCODE -ne 0) { throw 'Classic wheel build failed' }
python -m pip install --force-reinstall --no-deps `
  (Get-ChildItem $build -Filter "vibeocr_classic-$Version-*.whl" | Select-Object -First 1).FullName
if ($LASTEXITCODE -ne 0) { throw 'Classic wheel install failed' }
python -m pip install pyside6==6.11.1 qasync==0.28.0 numpy==2.5.1
if ($LASTEXITCODE -ne 0) { throw 'Classic runtime dependency install failed' }
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
    '--collect-submodules', 'vibeocr.backend',
    '--collect-data', 'vibeocr.runtime_contracts',
    '--collect-data', 'vibeocr.backend',
    '--add-data', "$root/resources;resources"
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
python -m PyInstaller @pyinstallerArgs
if ($LASTEXITCODE -ne 0) { throw 'Classic PyInstaller build failed' }
$product = Join-Path $dist 'VibeOCR'
python (Join-Path $root 'scripts/prune_pyside_artifact.py') --product-root $product
if ($LASTEXITCODE -ne 0) { throw 'Classic PySide6 payload pruning failed' }
python -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name updater --distpath (Join-Path $build 'updater-dist') `
  --workpath (Join-Path $build 'updater-work') `
  --specpath (Join-Path $build 'updater-spec') `
  (Join-Path $root 'scripts/updater_main.py')
if ($LASTEXITCODE -ne 0) { throw 'Classic updater build failed' }
Copy-Item -LiteralPath (Join-Path $build 'updater-dist/updater.exe') `
  -Destination $product
Copy-Item -LiteralPath (Join-Path $root 'LICENSE') -Destination $product
Copy-Item -LiteralPath (Join-Path $root 'CHANGELOG.md') -Destination $product
$zip = Join-Path $artifacts "VibeOCR-Classic-v$Version-win64.zip"
python (Join-Path $root 'scripts/package_product_release.py') `
  --product-root $product --frontend classic --frontend-version $Version `
  --source-commit (git -C $root rev-parse HEAD).Trim() `
  --component-lock $lock --protocol-release-dir $protocol `
  --backend-release-dir $backend --output $zip
if ($LASTEXITCODE -ne 0) { throw 'Classic product binding failed' }
python (Join-Path $root 'scripts/verify_pyside_artifact.py') $zip
if ($LASTEXITCODE -ne 0) { throw 'Classic artifact verification failed' }
Copy-Item -LiteralPath $lock -Destination $artifacts
python (Join-Path $root 'scripts/build_release_checksums.py') $artifacts `
  --sidecar-for $zip
if ($LASTEXITCODE -ne 0) { throw 'sidecar checksum build failed' }
Remove-Item -LiteralPath (Join-Path $artifacts 'SHA256SUMS') -Force
python (Join-Path $root 'scripts/build_spdx_sbom.py') --artifacts-dir $artifacts `
  --repository-name FelixJI/vibeocr-classic --version $Version
if ($LASTEXITCODE -ne 0) { throw 'SBOM build failed' }
python (Join-Path $root 'scripts/build_release_checksums.py') $artifacts
if ($LASTEXITCODE -ne 0) { throw 'checksum build failed' }
