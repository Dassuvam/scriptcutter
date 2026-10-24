# ScriptCutter installer for Windows.
#
# It runs three ways:
#   * from the download: "Install ScriptCutter.bat" starts app\install.ps1
#   * from the source code: powershell -ExecutionPolicy Bypass -File Windows\install.ps1
#   * online, without the "downloaded from the internet" warning (in PowerShell):
#       irm https://github.com/Dassuvam/scriptcutter/releases/latest/download/install-windows.ps1 | iex
#     It then fetches ScriptCutter-Windows.zip itself.
#
# Everything is installed for this user only, so no administrator rights are needed:
# Python (from python.org), ffmpeg and whisper.cpp (into %LOCALAPPDATA%\ScriptCutter\tools).
# Then install.py copies ScriptCutter into place, downloads the speech models, adds it to
# DaVinci Resolve, the Start menu, the desktop and Settings > Apps, checks it and opens it.
# Options go on to install.py, for example --yes or --models small,large-v3.

$ScriptCutterHere = if ($PSCommandPath) { Split-Path -Parent $PSCommandPath } else { "" }

function Install-ScriptCutter {
    param([string] $Here, [string[]] $Options)
    $ErrorActionPreference = "Stop"
    $ProgressPreference = "SilentlyContinue"   # PowerShell's progress bar makes downloads much slower
    $Repo = "Dassuvam/scriptcutter"
    $Tools = Join-Path $env:LOCALAPPDATA "ScriptCutter\tools"
    $Yes = $Options -contains "--yes"
    $Arm = $false
    try { $Arm = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture -eq "Arm64" } catch { }

    function Step($text) { Write-Host ""; Write-Host "== $text" }

    # native programs report through exit codes; "Stop" would turn their messages on stderr
    # (curl's progress bar, py's "no Python found") into errors when the output is redirected
    function Get-File($Url, $Dest) {
        $ErrorActionPreference = "Continue"
        # curl.exe is part of Windows 10 and 11: it shows progress and continues a broken download
        $part = "$Dest.part"
        & curl.exe -L --fail --progress-bar --retry 5 --retry-delay 3 -C - -o $part $Url
        if ($LASTEXITCODE -ne 0) {  # a finished .part from an earlier run can't be continued: start over once
            Remove-Item $part -Force -ErrorAction SilentlyContinue
            & curl.exe -L --fail --progress-bar --retry 5 --retry-delay 3 -o $part $Url
            if ($LASTEXITCODE -ne 0) { throw "The download of $Url stopped. Check the internet connection." }
        }
        Move-Item $part $Dest -Force
    }

    function Expand-Zip($Zip, $Dest) {
        $ErrorActionPreference = "Continue"
        if (Test-Path $Dest) { Remove-Item $Dest -Recurse -Force }
        New-Item -ItemType Directory -Force $Dest | Out-Null
        & tar.exe -xf $Zip -C $Dest   # much faster than Expand-Archive on big zips
        if ($LASTEXITCODE -ne 0) { Expand-Archive $Zip $Dest -Force -ErrorAction Stop }
    }

    function Refresh-Path {
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [Environment]::GetEnvironmentVariable("Path", "User")
    }

    function Find-Python {
        $ErrorActionPreference = "Continue"
        if (Get-Command py -ErrorAction SilentlyContinue) {
            $exe = & py -3 -c "import sys; print(sys.executable if sys.version_info >= (3, 9) else '')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
        }
        $own = Get-ChildItem (Join-Path $env:LOCALAPPDATA "Programs\Python\Python3*\python.exe") -ErrorAction SilentlyContinue |
               Sort-Object { [int]($_.Directory.Name -replace "\D", "") } -Descending | Select-Object -First 1
        if ($own) { return $own.FullName }
        return $null
    }

    function Install-Whisper($Patterns, $Dest) {
        # whisper.cpp publishes its Windows builds on separate "bNNNN" releases (version tags such as
        # v1.9.5 come without files), so take the newest release that has the wanted build
        $headers = @{ "User-Agent" = "ScriptCutter" }
        if ($env:GITHUB_TOKEN) { $headers["Authorization"] = "Bearer $env:GITHUB_TOKEN" }
        $releases = Invoke-RestMethod "https://api.github.com/repos/ggml-org/whisper.cpp/releases?per_page=40" -Headers $headers
        $asset = $null
        foreach ($pattern in $Patterns) {
            $asset = $releases | ForEach-Object { $_.assets } | Where-Object { $_.name -like $pattern } | Select-Object -First 1
            if ($asset) { break }
        }
        if (-not $asset) { throw "No whisper.cpp download matched $($Patterns -join ', ')." }
        Write-Host "  $($asset.name)"
        $zip = Join-Path $env:TEMP $asset.name
        Get-File $asset.browser_download_url $zip
        $unpacked = Join-Path $env:TEMP "scriptcutter-whisper"
        Expand-Zip $zip $unpacked
        $cli = Get-ChildItem $unpacked -Recurse -Filter "whisper-cli.exe" | Select-Object -First 1
        if (-not $cli) { throw "whisper-cli.exe was not in $($asset.name)." }
        New-Item -ItemType Directory -Force $Dest | Out-Null
        Copy-Item (Join-Path $cli.DirectoryName "*") $Dest -Recurse -Force
        Remove-Item $zip, $unpacked -Recurse -Force -ErrorAction SilentlyContinue
    }

    # install.py sits next to this file (download), one folder up (source code), or nowhere (online)
    $App = $null
    if ($Here) {
        foreach ($d in @($Here, (Split-Path -Parent $Here))) {
            if ($d -and (Test-Path (Join-Path $d "install.py"))) { $App = $d; break }
        }
    }
    if (-not $App) {
        Step "Downloading ScriptCutter"
        $setup = Join-Path $env:TEMP "ScriptCutter-setup"
        $zip = Join-Path $env:TEMP "ScriptCutter-Windows.zip"
        Get-File "https://github.com/$Repo/releases/latest/download/ScriptCutter-Windows.zip" $zip
        Expand-Zip $zip $setup
        $ErrorActionPreference = "Continue"
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $setup "ScriptCutter\app\install.ps1") @Options
        return
    }

    # files from a downloaded zip carry a "from the internet" mark: clear it so nothing asks again
    $download = Split-Path -Parent $App
    if (Test-Path (Join-Path $download "Install ScriptCutter.bat")) {
        Get-ChildItem -LiteralPath $download -Recurse -File | Unblock-File
    }

    Step "1/4  Python"
    $Python = Find-Python
    if (-not $Python) {
        $arch = if ($Arm) { "arm64" } else { "amd64" }
        $exe = Join-Path $env:TEMP "python-3.12.10-$arch.exe"
        Get-File "https://www.python.org/ftp/python/3.12.10/python-3.12.10-$arch.exe" $exe
        Write-Host "  Installing Python 3.12 for this user..."
        Start-Process $exe -Wait -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1",
                                               "InstallLauncherAllUsers=0", "Include_test=0", "Shortcuts=0"
        Remove-Item $exe -Force -ErrorAction SilentlyContinue
        Refresh-Path
        $Python = Find-Python
    }
    if (-not $Python) { throw "Python could not be installed. Install Python 3.12 from python.org, then run this again." }
    Write-Host "  $Python"

    Step "2/4  ffmpeg"
    $FfmpegBin = Join-Path $Tools "ffmpeg\bin"
    if ((Get-Command ffmpeg -ErrorAction SilentlyContinue) -or (Test-Path (Join-Path $FfmpegBin "ffmpeg.exe"))) {
        Write-Host "  already installed"
    } else {
        if ($Arm) {
            $url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-winarm64-gpl.zip"
        } else {
            $url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        }
        $zip = Join-Path $env:TEMP "scriptcutter-ffmpeg.zip"
        Get-File $url $zip
        $unpacked = Join-Path $env:TEMP "scriptcutter-ffmpeg"
        Expand-Zip $zip $unpacked
        $bin = Get-ChildItem $unpacked -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
        if (-not $bin) { throw "ffmpeg.exe was not in the ffmpeg download." }
        New-Item -ItemType Directory -Force $FfmpegBin | Out-Null
        Copy-Item (Join-Path $bin.DirectoryName "*") $FfmpegBin -Force
        Remove-Item $zip, $unpacked -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  $FfmpegBin"
    }

    Step "3/4  Speech recognition (whisper.cpp)"
    $WhisperDir = Join-Path $Tools "whisper"
    if (Test-Path (Join-Path $WhisperDir "whisper-cli.exe")) {
        Write-Host "  already installed"
    } elseif ($Arm) {
        Install-Whisper @("whisper-bin-win-cpu-arm64.zip") $WhisperDir
    } else {
        # the BLAS build is faster on the processor; the plain build is the fallback
        Install-Whisper @("whisper-blas-bin-x64.zip", "whisper-bin-x64.zip") $WhisperDir
    }
    # with an NVIDIA graphics card the GPU build is many times faster; install.py uses it only
    # after checking that it really runs on this PC (an old driver falls back to the processor)
    $CudaDir = Join-Path $Tools "whisper-cuda"
    $Nvidia = $false
    if (-not $Arm) {
        $Nvidia = [bool](Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "NVIDIA" })
    }
    if ($Nvidia -and -not (Test-Path (Join-Path $CudaDir "whisper-cli.exe"))) {
        $answer = if ($Yes) { "y" } else { Read-Host "  An NVIDIA graphics card was found. Download the GPU speech engine (about 650 MB, many times faster)? [Y/n]" }
        if ($answer -notmatch "^[nN]") { Install-Whisper @("whisper-bin-win-cuda-12*-x64.zip", "whisper-cublas-12*-bin-x64.zip") $CudaDir }
    }

    Step "4/4  ScriptCutter"
    $ErrorActionPreference = "Continue"
    & $Python (Join-Path $App "install.py") @Options
}

try {
    Install-ScriptCutter -Here $ScriptCutterHere -Options $args
} catch {
    Write-Host ""
    Write-Host "Something went wrong: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Run the installer again. If it happens again, send a photo of this window to dassuvam2005@gmail.com."
}
