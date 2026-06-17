param(
  [string[]]$SearchRoots = @(),
  [switch]$OpenDownloadPage,
  [switch]$Required
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$downloadUrl = "https://vb-audio.com/Cable/"
$pattern = "VB-Audio|VB-Cable|VoiceMeeter|Virtual Audio Cable|CABLE Input|CABLE Output|Cable A|Cable B|Cable C|Cable D"

function Get-VirtualAudioDevices {
  Get-CimInstance Win32_PnPEntity |
    Where-Object {
      ($_.PNPClass -eq "AudioEndpoint" -or $_.PNPClass -eq "MEDIA") -and
      ($_.Name -match $pattern)
    } |
    Select-Object Name, Status, PNPClass, Manufacturer
}

function Find-DriverPackage {
  $roots = @()
  foreach ($root in $SearchRoots) {
    if ($root -and (Test-Path $root)) {
      $roots += [System.IO.Path]::GetFullPath($root)
    }
  }

  $seen = @{}
  foreach ($root in $roots) {
    $key = $root.ToLowerInvariant()
    if ($seen.ContainsKey($key)) { continue }
    $seen[$key] = $true

    $directNames = @("VB虚拟.zip", "VBCABLE.zip", "VB-CABLE.zip", "VB-Audio-Cable.zip")
    foreach ($name in $directNames) {
      $candidate = Join-Path $root $name
      if (Test-Path $candidate) { return $candidate }
    }

    $match = Get-ChildItem -Path $root -Recurse -File -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -match "VB.*Cable|VB.*虚拟|VBCABLE" -and $_.Extension -eq ".zip" } |
      Select-Object -First 1
    if ($match) { return $match.FullName }
  }

  return ""
}

function Find-DriverInstaller {
  param([string]$PackagePath)

  $tempRoot = Join-Path $env:TEMP ("xiuxiulian-vbcable-" + [Guid]::NewGuid().ToString("N"))
  New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
  Expand-Archive -Path $PackagePath -DestinationPath $tempRoot -Force

  $installer = Get-ChildItem -Path $tempRoot -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in @("VBCABLE_Setup_x64.exe", "VBCABLE_Setup.exe") } |
    Sort-Object Name -Descending |
    Select-Object -First 1

  if (!$installer) {
    throw "Virtual audio driver installer was not found in $PackagePath"
  }

  return $installer.FullName
}

Write-Host "Checking virtual audio driver..."
$devices = Get-VirtualAudioDevices
if ($devices) {
  Write-Host "Virtual audio driver is installed."
  $devices | Format-Table -AutoSize
  exit 0
}

Write-Host "Virtual audio driver is not installed."
$package = Find-DriverPackage
if (!$package) {
  Write-Host "Driver package was not found in the app or backend folder."
  Write-Host "Official page: $downloadUrl"
  if ($OpenDownloadPage) {
    Start-Process $downloadUrl
  }

  if ($Required) {
    Write-Host "Install the driver package, then run setup-windows.bat again."
    exit 3
  }

  exit 2
}

Write-Host "Driver package: $package"
$installer = Find-DriverInstaller -PackagePath $package
Write-Host "Starting driver installer as administrator..."
Start-Process -FilePath $installer -Verb RunAs -Wait

Start-Sleep -Seconds 2
$devices = Get-VirtualAudioDevices
if ($devices) {
  Write-Host "Virtual audio driver is installed."
  $devices | Format-Table -AutoSize
  exit 0
}

Write-Host "Driver installer finished, but the device was not detected yet."
Write-Host "Restart Windows if the device does not appear, then run setup-windows.bat again."
exit 4
