param(
  [string]$VoiceRoot = $(if ($env:VOICE_ROOT) { $env:VOICE_ROOT } else { "D:\VoiceChanger" }),
  [string]$OutputPath = $(Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")) "release\VoiceChanger.zip")
)

$ErrorActionPreference = "Stop"
$resolvedVoiceRoot = [System.IO.Path]::GetFullPath($VoiceRoot)
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
$outputDir = Split-Path $resolvedOutput -Parent

if (!(Test-Path (Join-Path $resolvedVoiceRoot "runtime\python.exe"))) {
  throw "runtime\python.exe was not found under $resolvedVoiceRoot"
}

if (!(Test-Path (Join-Path $resolvedVoiceRoot "rvc_core"))) {
  throw "rvc_core was not found under $resolvedVoiceRoot"
}

New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

if (Test-Path $resolvedOutput) {
  Remove-Item -LiteralPath $resolvedOutput -Force
}

Write-Host "Packaging backend:"
Write-Host "  Source: $resolvedVoiceRoot"
Write-Host "  Output: $resolvedOutput"
Write-Host ""
Write-Host "Only publish this zip if you have the right to redistribute every included file and model."
Write-Host ""

Compress-Archive -Path (Join-Path $resolvedVoiceRoot "*") -DestinationPath $resolvedOutput -CompressionLevel Optimal

Write-Host "Done."
