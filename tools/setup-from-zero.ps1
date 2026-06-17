param(
  [string]$VoiceChangerZipUrl = $env:XIUXIULIAN_BACKEND_ZIP_URL,
  [switch]$OpenDriverDownload,
  [switch]$StartApp
)

$ErrorActionPreference = "Stop"
$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$settingsDir = Join-Path $env:APPDATA "xiuxiulian-voice-changer"
$settingsPath = Join-Path $settingsDir "settings.json"
$nodeUrl = "https://nodejs.org/"

function Write-Step($Message) {
  Write-Host ""
  Write-Host "== $Message =="
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
  Write-Step "Checking Node.js"
  Add-NodeToCurrentPath

  if ((Test-Command "node") -and (Test-Command "npm")) {
    Write-Host "Node.js is installed: $(node --version)"
    return
  }

  if (Test-Command "winget") {
    Write-Host "Node.js was not found. Installing Node.js LTS with winget..."
    winget install --id OpenJS.NodeJS.LTS -e --accept-package-agreements --accept-source-agreements
    Add-NodeToCurrentPath
  }

  if ((Test-Command "node") -and (Test-Command "npm")) {
    Write-Host "Node.js is ready: $(node --version)"
    return
  }

  Start-Process $nodeUrl
  throw "Node.js is still missing. Install the LTS version, reopen this script, and try again."
}

function Ensure-NpmDependencies {
  Write-Step "Checking app dependencies"
  Push-Location $projectRoot
  try {
    if (!(Test-Path (Join-Path $projectRoot "node_modules"))) {
      Write-Host "Installing npm dependencies..."
      npm install
    } else {
      Write-Host "node_modules already exists."
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
  $savedRoot = Get-SavedVoiceRoot
  $items = @(
    $env:VOICE_ROOT,
    $savedRoot,
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
    throw "Backend zip was not found: $zipFullPath"
  }

  $targetRoot = Join-Path $projectRoot "VoiceChanger"
  if (Test-Path $targetRoot) {
    throw "VoiceChanger folder already exists: $targetRoot"
  }

  $tempRoot = Join-Path $env:TEMP ("xiuxiulian-backend-" + [Guid]::NewGuid().ToString("N"))
  New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null

  Write-Host "Extracting backend zip..."
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
    throw "The zip does not contain a usable VoiceChanger folder with runtime\python.exe and rvc_core."
  }

  Copy-Item -Path $sourceRoot -Destination $targetRoot -Recurse
  return $targetRoot
}

function Ensure-VoiceChangerBackend {
  Write-Step "Checking VoiceChanger backend"

  $existingRoot = Find-VoiceRoot
  if ($existingRoot) {
    Write-Host "VoiceChanger backend is ready: $existingRoot"
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
      $root = Expand-VoiceChangerZip $zip
      Save-VoiceRoot $root
      return $root
    }
  }

  if ($VoiceChangerZipUrl) {
    $downloadPath = Join-Path $env:TEMP "VoiceChanger.zip"
    Write-Host "Downloading backend package..."
    Invoke-WebRequest -Uri $VoiceChangerZipUrl -OutFile $downloadPath
    $root = Expand-VoiceChangerZip $downloadPath
    Save-VoiceRoot $root
    return $root
  }

  Write-Host "VoiceChanger backend was not found."
  Write-Host ""
  Write-Host "Put one of these next to this app, then run setup-windows.bat again:"
  Write-Host "  1. A VoiceChanger folder containing runtime\python.exe and rvc_core"
  Write-Host "  2. A VoiceChanger.zip release package"
  Write-Host ""
  Write-Host "If you publish a backend zip in GitHub Releases, you can run:"
  Write-Host "  powershell -ExecutionPolicy Bypass -File tools\setup-from-zero.ps1 -VoiceChangerZipUrl <zip-url> -StartApp"
  return ""
}

function Ensure-VirtualAudio {
  Write-Step "Checking virtual audio driver"
  $script = Join-Path $PSScriptRoot "setup-virtual-audio.ps1"

  if ($OpenDriverDownload) {
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script -OpenDownloadPage
  } else {
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script
  }

  if ($LASTEXITCODE -eq 0) {
    return
  }

  Write-Host ""
  Write-Host "Virtual audio is not installed yet. You can still open the app, but games and voice chat usually need VB-CABLE."
}

function Start-App {
  Write-Step "Starting app"
  Push-Location $projectRoot
  try {
    Start-Process "http://127.0.0.1:5174/"
    npm run dev
  } finally {
    Pop-Location
  }
}

Ensure-Node
Ensure-NpmDependencies
$voiceRoot = Ensure-VoiceChangerBackend
Ensure-VirtualAudio

if (!$voiceRoot) {
  Write-Host ""
  Write-Host "Setup is incomplete because the VoiceChanger backend is missing."
  exit 2
}

Write-Host ""
Write-Host "Setup completed."

if ($StartApp) {
  Start-App
}
