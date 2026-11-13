# Build LimbusTranslator.exe (onefile, no console)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

python -m pip install -q -r requirements.txt pyinstaller

$addData = "glossary\terms.txt;glossary"
python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --onefile `
  --name LimbusTranslator `
  --collect-all winsdk `
  --hidden-import keyboard `
  --hidden-import PIL `
  --hidden-import mss `
  --hidden-import requests `
  --add-data $addData `
  limbus_translator.py

Copy-Item -Force "dist\LimbusTranslator.exe" ".\LimbusTranslator.exe"
Write-Host "Built: $((Get-Item .\LimbusTranslator.exe).FullName) ($([math]::Round((Get-Item .\LimbusTranslator.exe).Length/1MB,1)) MB)"
