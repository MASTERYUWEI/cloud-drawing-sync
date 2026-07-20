# 雲端圖資同步工具 — 一鍵建置 MSI
# 用法：powershell -ExecutionPolicy Bypass -File installer\build_msi.ps1
# 版本號單一來源：drive_sync_gui.py 的 APP_VERSION，改那裡即可。

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

# 1) 取版本號
$src = Get-Content -LiteralPath (Join-Path $root 'drive_sync_gui.py') -Raw -Encoding UTF8
if ($src -notmatch "APP_VERSION\s*=\s*'([0-9]+\.[0-9]+\.[0-9]+)'") {
    throw "drive_sync_gui.py 內找不到 APP_VERSION：必須是單引號三段數字格式，例如 APP_VERSION = '1.0.2'（不可用雙引號、不可加 rc/dev 後綴）"
}
$ver = $Matches[1]
Write-Host "=== 版本 v$ver ===" -ForegroundColor Cyan

# 2) PyInstaller 打包 exe
Write-Host "=== PyInstaller 建置 exe ===" -ForegroundColor Cyan
python -m PyInstaller --noconfirm --clean "雲端圖資同步工具_新版.spec"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失敗" }
$appdir = Join-Path $root 'dist\雲端圖資同步工具_新版'
if (-not (Test-Path -LiteralPath (Join-Path $appdir '雲端圖資同步工具_新版.exe'))) { throw "找不到 onedir 輸出 $appdir" }
if (-not (Test-Path -LiteralPath (Join-Path $appdir '_internal'))) { throw "找不到 $appdir\_internal" }

# 3) WiX 建置 MSI（asset 檔名用 ASCII，GitHub Release 網址較乾淨）
Write-Host "=== WiX 建置 MSI ===" -ForegroundColor Cyan
$msi = Join-Path $root "dist\CloudDrawingSync-$ver.msi"
wix build (Join-Path $PSScriptRoot 'Package.wxs') `
    -arch x64 `
    -d "ProductVersion=$ver" `
    -d "AppDir=$appdir" `
    -d "IconPath=$(Join-Path $PSScriptRoot 'app.ico')" `
    -o $msi
if ($LASTEXITCODE -ne 0) { throw "WiX 失敗" }

Get-Item -LiteralPath $msi | Select-Object Name, @{n='MB';e={[math]::Round($_.Length/1MB,1)}}
Write-Host "=== 完成：$msi ===" -ForegroundColor Green
Write-Host "發版：gh release create v$ver `"$msi`" --title `"v$ver`" --notes `"更新內容...`"" -ForegroundColor Yellow
