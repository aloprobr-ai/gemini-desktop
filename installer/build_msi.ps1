# Собирает MSI из уже готового exe.
# Версию берём из app.py, чтобы она не разъезжалась с тем, что видит апдейтер.
param([string]$Version = "")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$wix = "$env:USERPROFILE\.dotnet\tools\wix.exe"

if (-not $Version) {
    $line = Select-String -Path "$root\app.py" -Pattern '^APP_VERSION = "(.+)"' | Select-Object -First 1
    if (-not $line) { throw "не нашёл APP_VERSION в app.py" }
    $Version = $line.Matches[0].Groups[1].Value
}

$exe = "$root\dist\Gemini Desktop.exe"
if (-not (Test-Path $exe)) { throw "нет собранного exe: $exe" }

$out = "$root\dist\GeminiDesktop-$Version.msi"
Write-Output "версия $Version"
Write-Output "exe    $([math]::Round((Get-Item $exe).Length / 1MB, 1)) МБ"

& $wix build "$PSScriptRoot\GeminiDesktop.wxs" `
    -ext WixToolset.UI.wixext `
    -d AppVersion="$Version" `
    -d ExeFile="$exe" `
    -d IconFile="$root\icon.ico" `
    -d LicenseFile="$PSScriptRoot\license.rtf" `
    -arch x64 `
    -culture ru-RU `
    -o $out

if ($LASTEXITCODE -ne 0) { throw "wix build вернул $LASTEXITCODE" }

$size = [math]::Round((Get-Item $out).Length / 1MB, 1)
Write-Output "готово: $out ($size МБ)"
Write-Output ("sha256: " + (Get-FileHash $out -Algorithm SHA256).Hash.ToLower())
