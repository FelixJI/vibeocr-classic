[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
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
gh release download v2.0.0 --repo FelixJI/vibeocr-protocol --dir $protocol
if ($LASTEXITCODE -ne 0) { throw 'Protocol release download failed' }
gh release download v0.7.0 --repo FelixJI/vibeocr-backend --dir $backend
if ($LASTEXITCODE -ne 0) { throw 'Backend release download failed' }
foreach ($item in @(
    @{ path = $protocol; repo = 'FelixJI/vibeocr-protocol' },
    @{ path = $backend; repo = 'FelixJI/vibeocr-backend' }
)) {
    Get-ChildItem -LiteralPath $item.path -File |
      Where-Object Name -ne 'SHA256SUMS' |
      ForEach-Object {
        gh attestation verify $_.FullName --repo $item.repo
        if ($LASTEXITCODE -ne 0) { throw "attestation failed: $($_.Name)" }
      }
}
$lock = Join-Path $root 'component-lock.json'
if (-not (Test-Path -LiteralPath $lock -PathType Leaf)) {
    throw 'component-lock.json is required'
}
python -m pip install build==1.5.0 hatchling==1.27.0 pyinstaller==6.21.0
python -m pip install `
  (Get-ChildItem $protocol -Filter 'vibeocr_runtime_contracts-2.0.0-*.whl' | Select-Object -First 1).FullName `
  (Get-ChildItem $protocol -Filter 'vibeocr_runtime_client-2.0.0-*.whl' | Select-Object -First 1).FullName `
  (Get-ChildItem $backend -Filter 'vibeocr_backend-0.7.0-*.whl' | Select-Object -First 1).FullName
if ($LASTEXITCODE -ne 0) { throw 'verified upstream wheel install failed' }
python -m build --wheel --no-isolation (Join-Path $root 'apps/vibeocr-pyside') --outdir $build
if ($LASTEXITCODE -ne 0) { throw 'Classic wheel build failed' }
python -m pip install --force-reinstall --no-deps `
  (Get-ChildItem $build -Filter 'vibeocr_classic-0.7.0-*.whl' | Select-Object -First 1).FullName
if ($LASTEXITCODE -ne 0) { throw 'Classic wheel install failed' }
python -m pip install pyside6==6.11.1 qasync==0.28.0 numpy==2.5.1 pymupdf==1.28.0
if ($LASTEXITCODE -ne 0) { throw 'Classic runtime dependency install failed' }
$dist = Join-Path $build 'dist'
python -m PyInstaller --noconfirm --clean --onedir --windowed `
  --name VibeOCR --distpath $dist --workpath (Join-Path $build 'pyinstaller') `
  --specpath (Join-Path $build 'spec') `
  --collect-all PySide6 --collect-submodules vibeocr.classic `
  --collect-submodules vibeocr.runtime_client `
  --collect-submodules vibeocr.runtime_contracts `
  --collect-submodules vibeocr.backend `
  --collect-data vibeocr.runtime_contracts `
  --collect-data vibeocr.backend `
  --exclude-module torch --exclude-module torchvision `
  --exclude-module paddle --exclude-module cv2 `
  --exclude-module scipy --exclude-module sklearn --exclude-module pandas `
  --exclude-module transformers --exclude-module onnxruntime `
  --exclude-module tokenizers --exclude-module safetensors --exclude-module hf_xet `
  --add-data "$root/resources;resources" `
  (Join-Path $root 'scripts/classic_release_entry.py')
if ($LASTEXITCODE -ne 0) { throw 'Classic PyInstaller build failed' }
$product = Join-Path $dist 'VibeOCR'
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
$zip = Join-Path $artifacts 'VibeOCR-Classic-v0.7.0-win64.zip'
python (Join-Path $root 'scripts/package_product_release.py') `
  --product-root $product --frontend classic --frontend-version 0.7.0 `
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
  --repository-name FelixJI/vibeocr-classic --version 0.7.0
if ($LASTEXITCODE -ne 0) { throw 'SBOM build failed' }
python (Join-Path $root 'scripts/build_release_checksums.py') $artifacts
if ($LASTEXITCODE -ne 0) { throw 'checksum build failed' }
