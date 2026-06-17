param(
  [switch]$OpenDownloadPage
)

$ErrorActionPreference = "Stop"
$downloadUrl = "https://vb-audio.com/Cable/"
$pattern = "VB-Audio|VB-Cable|VoiceMeeter|Virtual Audio Cable|CABLE Input|CABLE Output|Cable A|Cable B|Cable C|Cable D"

Write-Host "Checking virtual audio devices..."

$devices = Get-CimInstance Win32_PnPEntity |
  Where-Object {
    ($_.PNPClass -eq "AudioEndpoint" -or $_.PNPClass -eq "MEDIA") -and
    ($_.Name -match $pattern)
  } |
  Select-Object Name, Status, PNPClass, Manufacturer

if ($devices) {
  Write-Host ""
  Write-Host "Virtual audio device detected:"
  $devices | Format-Table -AutoSize
  Write-Host "No installation is needed."
  exit 0
}

Write-Host ""
Write-Host "No VB-Audio / VoiceMeeter virtual audio device was detected."
Write-Host ""
Write-Host "Install steps:"
Write-Host "1. Download the Windows VB-CABLE package from the official page."
Write-Host "2. Unzip it."
Write-Host "3. Right-click VBCABLE_Setup_x64.exe and choose Run as administrator."
Write-Host "4. Click Install Driver."
Write-Host "5. Reopen this app. Reboot Windows if the new device does not appear."
Write-Host ""
Write-Host "Official page: $downloadUrl"

if ($OpenDownloadPage) {
  Start-Process $downloadUrl
}

exit 2
