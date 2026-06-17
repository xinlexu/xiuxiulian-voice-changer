param(
  [string]$VoiceChangerZipUrl = $env:XIUXIULIAN_BACKEND_ZIP_URL,
  [switch]$StartApp
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$settingsDir = Join-Path $env:APPDATA "xiuxiulian-voice-changer"
$settingsPath = Join-Path $settingsDir "settings.json"
$nodeUrl = "https://nodejs.org/"

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
  $resolved = [System.IO.Path]::GetFullPath($Root)
  return (Test-Path (Join-Path $resolved "runtime\python.exe")) -and
    (Test-Path (Join-Path $resolved "rvc_core"))
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

  Copy-Item -Path $sourceRoot -Destination $targetRoot -Recurse
  return $targetRoot
}

function Ensure-VoiceChangerBackend {
  Write-Step 1 "下载路径"
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
    $downloadPath = Join-Path $projectRoot "VoiceChanger.zip"
    Write-Host "Downloading backend package to: $downloadPath"
    Invoke-WebRequest -Uri $VoiceChangerZipUrl -OutFile $downloadPath
    $root = Expand-VoiceChangerZip $downloadPath
    Save-VoiceRoot $root
    return $root
  }

  Write-Host "Missing backend package."
  Write-Host "Put VoiceChanger.zip in this folder, then run setup-windows.bat again:"
  Write-Host "  $projectRoot"
  return ""
}

function Ensure-VirtualAudio {
  param([string]$VoiceRoot)

  Write-Step 2 "安装声卡驱动"
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
  Write-Step 3 "软件安装"
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
