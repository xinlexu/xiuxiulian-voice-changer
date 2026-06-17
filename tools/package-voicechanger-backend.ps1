param(
  [string]$VoiceRoot = $(if ($env:VOICE_ROOT) { $env:VOICE_ROOT } else { "D:\VoiceChanger" }),
  [string]$OutputPath = $(Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")) "release\VoiceChanger.zip")
)

$ErrorActionPreference = "Stop"
$resolvedVoiceRoot = [System.IO.Path]::GetFullPath($VoiceRoot)
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
$outputDir = Split-Path $resolvedOutput -Parent
$parent = Split-Path $resolvedVoiceRoot -Parent
$folderName = Split-Path $resolvedVoiceRoot -Leaf

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

$excludeArgs = @(
  "--exclude=$folderName/wuyuervc.exe",
  "--exclude=$folderName/wuyuervc.exe.WebView2",
  "--exclude=$folderName/AI降噪.exe",
  "--exclude=$folderName/runtime/**/*.lib",
  "--exclude=$folderName/runtime/**/*.pdb",
  "--exclude=$folderName/runtime/**/*.pyc",
  "--exclude=$folderName/runtime/**/__pycache__",
  "--exclude=$folderName/rvc_core/**/__pycache__",
  "--exclude=$folderName/rvc_core/**/*.pyc"
)

Write-Host "Packaging backend release zip:"
Write-Host "  Source: $resolvedVoiceRoot"
Write-Host "  Output: $resolvedOutput"
Write-Host "  Excluded: old shell exe, WebView, static libs, debug/cache files"
Write-Host ""
Write-Host "Only publish this zip if you have the right to redistribute every included runtime, model, and driver file."
Write-Host ""

& tar @excludeArgs -a -c -f $resolvedOutput -C $parent $folderName
if ($LASTEXITCODE -ne 0) {
  throw "tar failed with exit code $LASTEXITCODE"
}

$size = (Get-Item -LiteralPath $resolvedOutput).Length
Write-Host "Done. Size: $([math]::Round($size / 1GB, 2)) GB"
