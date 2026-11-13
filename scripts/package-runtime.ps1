param(
  [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
  [string]$OutputPath = (Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")).Path "release\XiuxiulianRuntime.zip"),
  [switch]$WithModels
)

# Packs what a new computer needs besides the app itself:
#   runtime\  dependencies\basemodels\  配置文件\vbcable\  (+ Assets\Models with -WithModels)
# Upload the zip (split into parts if your host has a size limit) and point
# scripts\setup.ps1 at it with XIUXIULIAN_BACKEND_ZIP_URL / _PART_URLS.
# Only publish files you are allowed to redistribute (models, driver, runtime).

$ErrorActionPreference = "Stop"
$items = @("runtime", "dependencies\basemodels", "配置文件\vbcable")
if ($WithModels) { $items += "Assets\Models" }
$stage = Join-Path $env:TEMP ("xiuxiulian-pack-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $stage | Out-Null
try {
  foreach ($rel in $items) {
    $src = Join-Path $Root $rel
    if (!(Test-Path -LiteralPath $src)) { throw "Missing: $src" }
    $dst = Join-Path $stage $rel
    New-Item -ItemType Directory -Force -Path (Split-Path $dst -Parent) | Out-Null
    & robocopy $src $dst /E /XD __pycache__ /XF *.pyc *.pdb /NFL /NDL /NJH /NJS /NP | Out-Null
  }
  New-Item -ItemType Directory -Force -Path (Split-Path $OutputPath -Parent) | Out-Null
  if (Test-Path -LiteralPath $OutputPath) { Remove-Item -LiteralPath $OutputPath -Force }
  & tar -a -c -f $OutputPath -C $stage .
  if ($LASTEXITCODE -ne 0) { throw "tar failed ($LASTEXITCODE)" }
  $size = (Get-Item -LiteralPath $OutputPath).Length
  Write-Host ("Done: {0} ({1:N2} GB)" -f $OutputPath, ($size / 1GB))
} finally {
  Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
}
