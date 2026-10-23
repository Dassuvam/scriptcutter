# ScriptCutter installer for Windows.
# Installs Python, ffmpeg and whisper.cpp, then runs install.py (models, Resolve menu, self-test).
# Started by "Install ScriptCutter.bat".

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # the progress bar makes downloads much slower
$App = Split-Path -Parent $MyInvocation.MyCommand.Path
$WhisperDir = Join-Path $env:LOCALAPPDATA "ScriptCutter\tools\whisper"

function Step($text) { Write-Host ""; Write-Host "== $text" }
function Have($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}
function Winget-Install($id) {
    if (-not (Have "winget")) {
        throw "winget is missing. Install 'App Installer' from the Microsoft Store, then run this again."
    }
    winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements
    Refresh-Path
}
function Find-Python {
    if (Have "py") { return (& py -3 -c "import sys; print(sys.executable)") }
    return $null
}

Step "1/3  Python"
$Python = Find-Python
if (-not $Python) {
    Winget-Install "Python.Python.3.12"
    $Python = Find-Python
}
if (-not $Python) { throw "Python could not be installed. Install it from python.org and run this again." }
Write-Host "  $Python"

Step "2/3  ffmpeg"
if (-not (Have "ffmpeg")) { Winget-Install "Gyan.FFmpeg" }
Write-Host "  ok"

Step "3/3  whisper.cpp"
if (-not (Test-Path (Join-Path $WhisperDir "whisper-cli.exe"))) {
    $release = Invoke-RestMethod "https://api.github.com/repos/ggml-org/whisper.cpp/releases/latest" `
        -Headers @{ "User-Agent" = "ScriptCutter" }
    $asset = $release.assets | Where-Object { $_.name -eq "whisper-bin-x64.zip" } | Select-Object -First 1
    if (-not $asset) { throw "whisper-bin-x64.zip was not found in the latest whisper.cpp release." }
    $zip = Join-Path $env:TEMP "scriptcutter-whisper.zip"
    $unpacked = Join-Path $env:TEMP "scriptcutter-whisper"
    Invoke-WebRequest $asset.browser_download_url -OutFile $zip
    if (Test-Path $unpacked) { Remove-Item $unpacked -Recurse -Force }
    Expand-Archive $zip $unpacked -Force
    $cli = Get-ChildItem $unpacked -Recurse -Filter "whisper-cli.exe" | Select-Object -First 1
    if (-not $cli) { throw "whisper-cli.exe was not in the whisper.cpp download." }
    New-Item -ItemType Directory -Force $WhisperDir | Out-Null
    Copy-Item (Join-Path $cli.DirectoryName "*") $WhisperDir -Recurse -Force
}
Write-Host "  $WhisperDir"

& $Python (Join-Path $App "install.py") @args
