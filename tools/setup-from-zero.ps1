param(
  [string]$VoiceChangerZipUrl = $env:XIUXIULIAN_BACKEND_ZIP_URL,
  [string[]]$VoiceChangerPartUrls = @(),
  [switch]$StartApp
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$settingsDir = Join-Path $env:APPDATA "xiuxiulian-voice-changer"
$settingsPath = Join-Path $settingsDir "settings.json"
$nodeUrl = "https://nodejs.org/"
$setupVersion = "2026-06-17-resume-download"
$defaultBackendReleaseTag = $(if ($env:XIUXIULIAN_BACKEND_RELEASE_TAG) { $env:XIUXIULIAN_BACKEND_RELEASE_TAG } else { "backend-v2" })
$defaultBackendPartCount = $(if ($env:XIUXIULIAN_BACKEND_PART_COUNT) { [int]$env:XIUXIULIAN_BACKEND_PART_COUNT } else { 22 })
$script:curlSupportsRetryAllErrors = $null

function Write-Step {
  param(
    [int]$Number,
    [string]$Message
  )

  Write-Host ""
  Write-Host "[$Number/3] $Message"
}

function Test-Command($Name) {
  return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-CurlRetryAllErrors {
  if ($null -ne $script:curlSupportsRetryAllErrors) {
    return $script:curlSupportsRetryAllErrors
  }

  try {
    $help = & curl.exe --help all 2>$null
    $script:curlSupportsRetryAllErrors = [bool]($help | Select-String -SimpleMatch "--retry-all-errors" -Quiet)
  } catch {
    $script:curlSupportsRetryAllErrors = $false
  }

  return $script:curlSupportsRetryAllErrors
}

function Get-DefaultBackendPartUrls {
  for ($i = 1; $i -le $defaultBackendPartCount; $i++) {
    $partName = "VoiceChanger.zip.{0:D3}" -f $i
    "https://github.com/xinlexu/xiuxiulian-voice-changer/releases/download/$defaultBackendReleaseTag/$partName"
  }
}

function Get-DownloadFileName($Url, $FallbackName) {
  try {
    $uri = [Uri]$Url
    $name = [System.IO.Path]::GetFileName($uri.AbsolutePath)
    if ($name) {
      return $name
    }
  } catch {
  }

  return $FallbackName
}

function Invoke-SetupDownload($Url, $OutputPath) {
  $target = [System.IO.Path]::GetFullPath($OutputPath)
  $targetDir = Split-Path $target -Parent
  New-Item -ItemType Directory -Force -Path $targetDir | Out-Null

  if (Test-Path -LiteralPath $target) {
    $existingTarget = Get-Item -LiteralPath $target
    if ($existingTarget.Length -gt 0) {
      Write-Host "Already downloaded: $(Split-Path $target -Leaf)"
      return
    }

    Remove-Item -LiteralPath $target -Force
  }

  $tempPath = "$target.download"
  $maxAttempts = 12

  for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
    $resumeBytes = 0
    if (Test-Path -LiteralPath $tempPath) {
      $resumeBytes = (Get-Item -LiteralPath $tempPath).Length
    }

    $resumeText = ""
    if ($resumeBytes -gt 0) {
      $resumeText = ", resume $([math]::Round($resumeBytes / 1MB, 1)) MB"
    }

    Write-Host "Downloading: $(Split-Path $target -Leaf) (try $attempt/$maxAttempts$resumeText)"

    if (Test-Command "curl.exe") {
      $curlArgs = @(
        "-L",
        "--fail",
        "--connect-timeout", "30",
        "--retry", "3",
        "--retry-delay", "5",
        "--speed-time", "60",
        "--speed-limit", "1024"
      )

      if (Test-CurlRetryAllErrors) {
        $curlArgs += "--retry-all-errors"
      }

      if ($resumeBytes -gt 0) {
        $curlArgs += @("-C", "-")
      }

      $curlArgs += @("-o", $tempPath, $Url)
      & curl.exe @curlArgs
      $exitCode = $LASTEXITCODE

      if ($exitCode -eq 0) {
        Move-Item -LiteralPath $tempPath -Destination $target -Force
        return
      }

      if ($exitCode -eq 33 -and (Test-Path -LiteralPath $tempPath)) {
        Write-Host "Server refused resume; restarting this part from zero."
        Remove-Item -LiteralPath $tempPath -Force
      }
    } else {
      try {
        if (Test-Path -LiteralPath $tempPath) {
          Remove-Item -LiteralPath $tempPath -Force
        }
        Invoke-WebRequest -Uri $Url -OutFile $tempPath -UseBasicParsing
        Move-Item -LiteralPath $tempPath -Destination $target -Force
        return
      } catch {
        Write-Host "Download attempt failed: $($_.Exception.Message)"
      }
    }

    if ($attempt -lt $maxAttempts) {
      Start-Sleep -Seconds ([math]::Min(60, 5 * $attempt))
    }
  }

  throw "Download failed after $maxAttempts attempts: $Url"
}

function Get-BackendPartUrls {
  if ($VoiceChangerPartUrls -and $VoiceChangerPartUrls.Count -gt 0) {
    return $VoiceChangerPartUrls
  }

  if ($env:XIUXIULIAN_BACKEND_PART_URLS) {
    return @(
      $env:XIUXIULIAN_BACKEND_PART_URLS -split "[;`r`n]+" |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ }
    )
  }

  return @(Get-DefaultBackendPartUrls)
}

function Merge-BackendParts($PartPaths, $OutputPath) {
  $zipFullPath = [System.IO.Path]::GetFullPath($OutputPath)
  if (Test-Path -LiteralPath $zipFullPath) {
    Remove-Item -LiteralPath $zipFullPath -Force
  }

  Write-Host "Merging backend package..."
  $outStream = [System.IO.File]::Create($zipFullPath)
  try {
    foreach ($part in $PartPaths) {
      $inStream = [System.IO.File]::OpenRead($part)
      try {
        $inStream.CopyTo($outStream)
      } finally {
        $inStream.Dispose()
      }
    }
  } finally {
    $outStream.Dispose()
  }

  return $zipFullPath
}

function Download-BackendParts {
  $partUrls = @(Get-BackendPartUrls)
  if (!$partUrls -or $partUrls.Count -eq 0) {
    return ""
  }

  Write-Host "Backend package parts: $($partUrls.Count)"

  $downloadDir = Join-Path $projectRoot ".xiuxiulian-downloads"
  New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null

  $partPaths = @()
  for ($i = 0; $i -lt $partUrls.Count; $i++) {
    $fallbackName = "VoiceChanger.zip.{0:D3}" -f ($i + 1)
    $fileName = Get-DownloadFileName $partUrls[$i] $fallbackName
    $partPath = Join-Path $downloadDir $fileName
    Invoke-SetupDownload $partUrls[$i] $partPath
    $partPaths += $partPath
  }

  return Merge-BackendParts $partPaths (Join-Path $downloadDir "VoiceChanger.zip")
}

function Add-NodeToCurrentPath {
  $nodeDir = Join-Path $env:ProgramFiles "nodejs"
  if ((Test-Path $nodeDir) -and ($env:Path -notlike "*$nodeDir*")) {
    $env:Path = "$nodeDir;$env:Path"
  }
}

function Ensure-Node {
  Add-NodeToCurrentPath

  if ((Test-Command "node") -and (Test-Command "npm")) {
    Write-Host "Node.js is installed: $(node --version)"
    return
  }

  if (Test-Command "winget") {
    Write-Host "Installing Node.js LTS..."
    winget install --id OpenJS.NodeJS.LTS -e --accept-package-agreements --accept-source-agreements
    Add-NodeToCurrentPath
  }

  if ((Test-Command "node") -and (Test-Command "npm")) {
    Write-Host "Node.js is ready: $(node --version)"
    return
  }

  Start-Process $nodeUrl
  throw "Node.js installation is not complete. Install Node.js LTS, then run setup-windows.bat again."
}

function Ensure-NpmDependencies {
  Push-Location $projectRoot
  try {
    if (!(Test-Path (Join-Path $projectRoot "node_modules"))) {
      Write-Host "Installing app dependencies..."
      npm install
    } else {
      Write-Host "App dependencies are installed."
    }
  } finally {
    Pop-Location
  }
}

function Test-VoiceRoot($Root) {
  if (!$Root) { return $false }
  try {
    $resolved = [System.IO.Path]::GetFullPath($Root)
    $python = [System.IO.Path]::Combine($resolved, "runtime", "python.exe")
    $core = [System.IO.Path]::Combine($resolved, "rvc_core")
    return (Test-Path -LiteralPath $python) -and (Test-Path -LiteralPath $core)
  } catch {
    return $false
  }
}

function Get-SavedVoiceRoot {
  if (!(Test-Path $settingsPath)) { return "" }

  try {
    $settings = Get-Content $settingsPath -Raw | ConvertFrom-Json
    return [string]$settings.voiceRoot
  } catch {
    return ""
  }
}

function Get-VoiceRootCandidates {
  $items = @(
    $env:VOICE_ROOT,
    (Get-SavedVoiceRoot),
    (Join-Path $projectRoot "VoiceChanger"),
    (Join-Path (Split-Path $projectRoot -Parent) "VoiceChanger"),
    "D:\VoiceChanger",
    "C:\VoiceChanger",
    "E:\VoiceChanger",
    "F:\VoiceChanger"
  )

  $seen = @{}
  foreach ($item in $items) {
    if (!$item) { continue }
    $full = [System.IO.Path]::GetFullPath($item)
    $key = $full.ToLowerInvariant()
    if (!$seen.ContainsKey($key)) {
      $seen[$key] = $true
      $full
    }
  }
}

function Find-VoiceRoot {
  foreach ($candidate in Get-VoiceRootCandidates) {
    if (Test-VoiceRoot $candidate) {
      return $candidate
    }
  }
  return ""
}

function Save-VoiceRoot($Root) {
  New-Item -ItemType Directory -Force -Path $settingsDir | Out-Null
  $payload = @{ voiceRoot = [System.IO.Path]::GetFullPath($Root) } | ConvertTo-Json -Depth 3
  Set-Content -Path $settingsPath -Value $payload -Encoding UTF8
}

function Expand-VoiceChangerZip($ZipPath) {
  $zipFullPath = [System.IO.Path]::GetFullPath($ZipPath)
  if (!(Test-Path $zipFullPath)) {
    throw "Backend package was not found: $zipFullPath"
  }

  $targetRoot = Join-Path $projectRoot "VoiceChanger"
  if (Test-Path $targetRoot) {
    throw "VoiceChanger folder already exists: $targetRoot"
  }

  $tempRoot = Join-Path $env:TEMP ("xiuxiulian-backend-" + [Guid]::NewGuid().ToString("N"))
  New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null

  try {
    Write-Host "Extracting backend package..."
    Expand-Archive -Path $zipFullPath -DestinationPath $tempRoot -Force

    $sourceRoot = ""
    if (Test-VoiceRoot $tempRoot) {
      $sourceRoot = $tempRoot
    } elseif (Test-VoiceRoot (Join-Path $tempRoot "VoiceChanger")) {
      $sourceRoot = Join-Path $tempRoot "VoiceChanger"
    } else {
      $match = Get-ChildItem -Path $tempRoot -Directory -Recurse -ErrorAction SilentlyContinue |
        Where-Object { Test-VoiceRoot $_.FullName } |
        Select-Object -First 1
      if ($match) {
        $sourceRoot = $match.FullName
      }
    }

    if (!$sourceRoot) {
      throw "VoiceChanger.zip does not contain runtime\python.exe and rvc_core."
    }

    if ($sourceRoot -eq $tempRoot) {
      New-Item -ItemType Directory -Force -Path $targetRoot | Out-Null
      Get-ChildItem -LiteralPath $tempRoot -Force | Move-Item -Destination $targetRoot
    } else {
      Move-Item -LiteralPath $sourceRoot -Destination $targetRoot
    }

    return $targetRoot
  } finally {
    if (Test-Path -LiteralPath $tempRoot) {
      Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
  }
}

function Ensure-VoiceChangerBackend {
  Write-Step 1 "Download path"
  Write-Host "Install folder: $projectRoot"

  $existingRoot = Find-VoiceRoot
  if ($existingRoot) {
    Write-Host "Backend folder: $existingRoot"
    Save-VoiceRoot $existingRoot
    return $existingRoot
  }

  $localZips = @(
    (Join-Path $projectRoot "VoiceChanger.zip"),
    (Join-Path $projectRoot "VoiceChanger-runtime.zip"),
    (Join-Path $projectRoot "backend.zip")
  )

  foreach ($zip in $localZips) {
    if (Test-Path $zip) {
      Write-Host "Backend package: $zip"
      $root = Expand-VoiceChangerZip $zip
      Save-VoiceRoot $root
      return $root
    }
  }

  if ($VoiceChangerZipUrl) {
    $downloadDir = Join-Path $projectRoot ".xiuxiulian-downloads"
    $downloadPath = Join-Path $downloadDir "VoiceChanger.zip"
    Write-Host "Downloading backend package to: $downloadPath"
    Invoke-SetupDownload $VoiceChangerZipUrl $downloadPath
    try {
      $root = Expand-VoiceChangerZip $downloadPath
      Save-VoiceRoot $root
      return $root
    } finally {
      if (Test-Path -LiteralPath $downloadDir) {
        Remove-Item -LiteralPath $downloadDir -Recurse -Force
      }
    }
  }

  $downloadedZip = Download-BackendParts
  if ($downloadedZip) {
    $downloadDir = Split-Path $downloadedZip -Parent
    try {
      $root = Expand-VoiceChangerZip $downloadedZip
      Save-VoiceRoot $root
      return $root
    } finally {
      if (Test-Path -LiteralPath $downloadDir) {
        Remove-Item -LiteralPath $downloadDir -Recurse -Force
      }
    }
  }

  throw "Backend package is not configured. Set XIUXIULIAN_BACKEND_ZIP_URL or XIUXIULIAN_BACKEND_PART_URLS, then run setup-windows.bat again."
}

function Ensure-VirtualAudio {
  param([string]$VoiceRoot)

  Write-Step 2 "Install audio driver"
  $script = Join-Path $PSScriptRoot "setup-virtual-audio.ps1"
  $searchRoots = @($projectRoot, (Join-Path $projectRoot "VoiceChanger"))
  if ($VoiceRoot) {
    $searchRoots += $VoiceRoot
  }

  powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script -SearchRoots $searchRoots -OpenDownloadPage -Required
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}

function Ensure-AppInstalled {
  Write-Step 3 "Install app"
  Ensure-Node
  Ensure-NpmDependencies
}

function Start-App {
  Push-Location $projectRoot
  try {
    Start-Process "http://127.0.0.1:5174/"
    npm run dev
  } finally {
    Pop-Location
  }
}

Write-Host "Setup version: $setupVersion"

$voiceRoot = Ensure-VoiceChangerBackend
if (!$voiceRoot) {
  exit 2
}

Ensure-VirtualAudio -VoiceRoot $voiceRoot
Ensure-AppInstalled

Write-Host ""
Write-Host "Setup completed."

if ($StartApp) {
  Start-App
}
