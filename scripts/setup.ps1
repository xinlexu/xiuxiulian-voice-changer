param(
  [string]$VoiceChangerZipUrl = $env:XIUXIULIAN_BACKEND_ZIP_URL,
  [string[]]$VoiceChangerPartUrls = @(),
  [switch]$StartApp
)

# 羞羞脸变声 — first-time setup for a fresh computer.
# The app itself (羞羞脸变声.exe + app\) comes from the GitHub zip. This script
# downloads the runtime package (Python + PyTorch + base models + voice models),
# merges it into this folder, installs the virtual audio driver and starts the app.
# Re-running is safe: finished downloads are skipped, broken ones resume.

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$setupVersion = "2026-11-13-backend-v3"
$defaultBackendReleaseTag = $(if ($env:XIUXIULIAN_BACKEND_RELEASE_TAG) { $env:XIUXIULIAN_BACKEND_RELEASE_TAG } else { "backend-v3" })
$defaultBackendPartCount = $(if ($env:XIUXIULIAN_BACKEND_PART_COUNT) { [int]$env:XIUXIULIAN_BACKEND_PART_COUNT } else { 22 })
$parallelBackendDownloads = $(if ($env:XIUXIULIAN_PARALLEL_DOWNLOADS) { [int]$env:XIUXIULIAN_PARALLEL_DOWNLOADS } else { 4 })
$defaultBackendZipBytes = 5509343662
$defaultBackendZipSha256 = "4D2E7E3472EDFBCFE4E603C7C235009852730765599F0D6C8CB9DB07235757E3"
$script:curlSupportsRetryAllErrors = $null
$script:aria2Command = $null

# Parts of the old 五月变声 package that the new engine does not use.
$legacyItems = @("wuyuervc.exe", "wuyuervc.exe.WebView2", "AI降噪.exe", "rvc_core", "Launchers", "AppSettings",
  "configs", "tools", "userconfig.json", "配置文件\voice.exe", "dependencies\infer_lib", "dependencies\tools",
  "dependencies\__pycache__", "dependencies\__init__.py", "Assets\PremiumModels", "配置文件\111")

function Write-Step {
  param([int]$Number, [string]$Message)
  Write-Host ""
  Write-Host "[$Number/3] $Message"
}

function Test-Command($Name) {
  return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-Aria2Command {
  if ($script:aria2Command -and (Test-Path -LiteralPath $script:aria2Command)) {
    return $script:aria2Command
  }

  $command = Get-Command "aria2c.exe" -ErrorAction SilentlyContinue
  if ($command) {
    $script:aria2Command = $command.Source
    return $script:aria2Command
  }

  $candidatePaths = @()
  foreach ($programRoot in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
    if (!$programRoot) { continue }
    $candidatePaths += (Join-Path $programRoot "aria2\aria2c.exe")
    $candidatePaths += (Join-Path $programRoot "aria2c\aria2c.exe")
  }

  foreach ($candidate in $candidatePaths) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) {
      $script:aria2Command = $candidate
      return $script:aria2Command
    }
  }

  $wingetPackages = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
  if (Test-Path -LiteralPath $wingetPackages) {
    $match = Get-ChildItem -Path $wingetPackages -Directory -Filter "aria2.aria2_*" -ErrorAction SilentlyContinue |
      Sort-Object LastWriteTime -Descending |
      ForEach-Object {
        Get-ChildItem -Path $_.FullName -Filter "aria2c.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
      } |
      Select-Object -First 1

    if ($match) {
      $script:aria2Command = $match.FullName
      return $script:aria2Command
    }
  }

  return ""
}

function Ensure-Aria2 {
  return Get-Aria2Command
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

function Get-DownloadMirrors {
  if ($env:XIUXIULIAN_DOWNLOAD_MIRRORS) {
    return @(
      $env:XIUXIULIAN_DOWNLOAD_MIRRORS -split "[;`r`n]+" |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -or $_ -eq "direct" }
    )
  }

  return @(
    "https://gh-proxy.com/",
    "https://ghproxy.net/",
    "direct"
  )
}

function Get-DownloadUrls($Url) {
  if ($Url -notlike "https://github.com/*") {
    return @($Url)
  }

  $seen = @{}
  $urls = @()
  foreach ($mirror in Get-DownloadMirrors) {
    if (!$mirror -or $mirror -eq "direct") {
      $candidate = $Url
    } elseif ($mirror -like "*{0}*") {
      $candidate = [string]::Format($mirror, $Url)
    } else {
      $candidate = "$mirror$Url"
    }

    $key = $candidate.ToLowerInvariant()
    if (!$seen.ContainsKey($key)) {
      $seen[$key] = $true
      $urls += $candidate
    }
  }

  return $urls
}

function Get-DownloadSourceLabel($Url) {
  try {
    $uri = [Uri]$Url
    if ($uri.Host -eq "github.com") {
      return "github"
    }
    return $uri.Host
  } catch {
    return "source"
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

function Invoke-Aria2Download($Url, $OutputPath, $TempPath) {
  $aria2 = Ensure-Aria2
  if (!$aria2) {
    return $false
  }

  $targetDir = Split-Path ([System.IO.Path]::GetFullPath($OutputPath)) -Parent
  $tempName = Split-Path $TempPath -Leaf
  Write-Host "Using accelerated download: aria2c"

  foreach ($candidateUrl in Get-DownloadUrls $Url) {
    Write-Host "Download source: $(Get-DownloadSourceLabel $candidateUrl)"
    & $aria2 `
      "--continue=true" `
      "--max-connection-per-server=8" `
      "--split=8" `
      "--min-split-size=4M" `
      "--max-tries=20" `
      "--retry-wait=5" `
      "--connect-timeout=30" `
      "--timeout=60" `
      "--lowest-speed-limit=1K" `
      "--file-allocation=none" `
      "--allow-overwrite=true" `
      "--auto-file-renaming=false" `
      "--summary-interval=10" `
      "--console-log-level=warn" `
      "--dir=$targetDir" `
      "--out=$tempName" `
      $candidateUrl

    if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $TempPath)) {
      Move-Item -LiteralPath $TempPath -Destination $OutputPath -Force
      $controlPath = "$TempPath.aria2"
      if (Test-Path -LiteralPath $controlPath) {
        Remove-Item -LiteralPath $controlPath -Force
      }
      return $true
    }
  }

  Write-Host "Accelerated download did not complete; using built-in downloader."
  return $false
}

function New-CurlArgumentList($Url, $TempPath, $ResumeBytes) {
  $curlArgs = @(
    "-L",
    "--fail",
    "--silent",
    "--show-error",
    "--connect-timeout", "30",
    "--retry", "8",
    "--retry-delay", "5",
    "--speed-time", "60",
    "--speed-limit", "1024"
  )

  if (Test-CurlRetryAllErrors) {
    $curlArgs += "--retry-all-errors"
  }

  if ($ResumeBytes -gt 0) {
    $curlArgs += @("-C", "-")
  }

  return $curlArgs + @("-o", $TempPath, $Url)
}

function Invoke-CurlDownload($Url, $OutputPath, $TempPath) {
  $resumeBytes = 0
  if (Test-Path -LiteralPath $TempPath) {
    $resumeBytes = (Get-Item -LiteralPath $TempPath).Length
  }

  $downloadExitCode = 1
  foreach ($candidateUrl in Get-DownloadUrls $Url) {
    Write-Host "Download source: $(Get-DownloadSourceLabel $candidateUrl)"
    $curlArgs = New-CurlArgumentList $candidateUrl $TempPath $resumeBytes
    & curl.exe @curlArgs
    $downloadExitCode = $LASTEXITCODE
    if ($downloadExitCode -eq 0) {
      return 0
    }
  }

  return $downloadExitCode
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
  if (Invoke-Aria2Download $Url $target $tempPath) {
    return
  }

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
      $exitCode = Invoke-CurlDownload $Url $target $tempPath

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

function Invoke-ParallelCurlDownloads($Downloads) {
  if (!(Test-Command "curl.exe")) {
    return $false
  }

  $maxParallel = [Math]::Max(1, [Math]::Min(8, $parallelBackendDownloads))
  $maxRounds = 12
  $pending = @($Downloads)
  $running = @()

  Write-Host "Parallel downloads: $maxParallel"

  for ($round = 1; $round -le $maxRounds; $round++) {
    $retry = @()
    while ($pending.Count -gt 0 -or $running.Count -gt 0) {
      while ($pending.Count -gt 0 -and $running.Count -lt $maxParallel) {
        $item = $pending[0]
        $pending = @($pending | Select-Object -Skip 1)

        if (Test-Path -LiteralPath $item.TargetPath) {
          $existingTarget = Get-Item -LiteralPath $item.TargetPath
          if ($existingTarget.Length -gt 0) {
            Write-Host "Already downloaded: $($item.Name)"
            continue
          }

          Remove-Item -LiteralPath $item.TargetPath -Force
        }

        $resumeBytes = 0
        if (Test-Path -LiteralPath $item.TempPath) {
          $resumeBytes = (Get-Item -LiteralPath $item.TempPath).Length
        }

        $resumeText = ""
        if ($resumeBytes -gt 0) {
          $resumeText = ", resume $([math]::Round($resumeBytes / 1MB, 1)) MB"
        }

        $candidateUrls = @($item.Urls)
        if (!$candidateUrls -or $candidateUrls.Count -eq 0) {
          $candidateUrls = @(Get-DownloadUrls $item.Url)
        }
        $urlIndex = [Math]::Min($round - 1, $candidateUrls.Count - 1)
        $downloadUrl = $candidateUrls[$urlIndex]

        Write-Host "Downloading: $($item.Name) ($(Get-DownloadSourceLabel $downloadUrl), round $round/$maxRounds$resumeText)"
        $arguments = New-CurlArgumentList $downloadUrl $item.TempPath $resumeBytes
        $stdout = "$($item.TempPath).out.log"
        $stderr = "$($item.TempPath).err.log"

        Remove-Item -LiteralPath $stdout,$stderr -Force -ErrorAction SilentlyContinue
        $quotedArguments = @($arguments | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' })
        $process = Start-Process -FilePath "curl.exe" -ArgumentList $quotedArguments -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        # Cache the process handle before it exits so Windows PowerShell preserves ExitCode.
        $null = $process.Handle
        $running += [pscustomobject]@{
          Item = $item
          Process = $process
          StdErr = $stderr
        }
      }

      if ($running.Count -eq 0) {
        break
      }

      Start-Sleep -Milliseconds 500
      $stillRunning = @()
      foreach ($entry in $running) {
        $entry.Process.Refresh()
        if (!$entry.Process.HasExited) {
          $stillRunning += $entry
          continue
        }

        $entry.Process.WaitForExit()
        $exitCode = $entry.Process.ExitCode
        $item = $entry.Item
        if ($exitCode -eq 0 -and (Test-Path -LiteralPath $item.TempPath)) {
          Move-Item -LiteralPath $item.TempPath -Destination $item.TargetPath -Force
          Remove-Item -LiteralPath "$($item.TempPath).aria2",$entry.StdErr,"$($item.TempPath).out.log" -Force -ErrorAction SilentlyContinue
          Write-Host "Downloaded: $($item.Name)"
          continue
        }

        if ($exitCode -eq 33 -and (Test-Path -LiteralPath $item.TempPath)) {
          Write-Host "Server refused resume for $($item.Name); restarting this part from zero."
          Remove-Item -LiteralPath $item.TempPath -Force
        }

        $err = ""
        if (Test-Path -LiteralPath $entry.StdErr) {
          $err = ([string](Get-Content -LiteralPath $entry.StdErr -Raw -ErrorAction SilentlyContinue)).Trim()
        }
        if ($err) {
          Write-Host "Download failed: $($item.Name) ($err)"
        } else {
          Write-Host "Download failed: $($item.Name) (exit $exitCode)"
        }

        if ($round -lt $maxRounds) {
          $retry += $item
        } else {
          throw "Download failed after $maxRounds rounds: $($item.Url)"
        }
      }

      $running = $stillRunning
    }

    $pending = @($retry)
    if ($pending.Count -eq 0 -and $running.Count -eq 0) {
      return $true
    }

    Start-Sleep -Seconds ([math]::Min(60, 5 * $round))
  }

  throw "Download failed after $maxRounds rounds."
}

function Test-UsingDefaultBackendParts {
  return (!$VoiceChangerPartUrls -or $VoiceChangerPartUrls.Count -eq 0) -and !$env:XIUXIULIAN_BACKEND_PART_URLS
}

function Assert-BackendZipIntegrity($ZipPath) {
  if (!(Test-UsingDefaultBackendParts)) {
    return
  }

  $zip = Get-Item -LiteralPath $ZipPath
  if ($zip.Length -ne $defaultBackendZipBytes) {
    throw "Backend package size is wrong. Expected $defaultBackendZipBytes bytes, got $($zip.Length). Re-run the first-time setup batch file to download again."
  }

  Write-Host "Verifying backend package..."
  $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ZipPath).Hash.ToUpperInvariant()
  if ($hash -ne $defaultBackendZipSha256) {
    throw "Backend package checksum failed. Re-run the first-time setup batch file to download again."
  }
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
  $downloads = @()
  for ($i = 0; $i -lt $partUrls.Count; $i++) {
    $fallbackName = "VoiceChanger.zip.{0:D3}" -f ($i + 1)
    $fileName = Get-DownloadFileName $partUrls[$i] $fallbackName
    $partPath = Join-Path $downloadDir $fileName
    $downloads += [pscustomobject]@{
      Url = $partUrls[$i]
      Urls = @(Get-DownloadUrls $partUrls[$i])
      Name = $fileName
      TargetPath = [System.IO.Path]::GetFullPath($partPath)
      TempPath = [System.IO.Path]::GetFullPath("$partPath.download")
    }
    $partPaths += $partPath
  }

  if (!(Invoke-ParallelCurlDownloads $downloads)) {
    foreach ($download in $downloads) {
      Invoke-SetupDownload $download.Url $download.TargetPath
    }
  }

  $zipPath = Merge-BackendParts $partPaths (Join-Path $downloadDir "VoiceChanger.zip")
  Assert-BackendZipIntegrity $zipPath
  # the parts are no longer needed once the merged package checks out (saves 5.5 GB of disk)
  foreach ($part in $partPaths) { Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue }
  return $zipPath
}


function Test-RuntimeReady {
  return (Test-Path -LiteralPath (Join-Path $projectRoot "runtime\python.exe")) -and
    (Test-Path -LiteralPath (Join-Path $projectRoot "runtime\pythonw.exe")) -and
    (Test-Path -LiteralPath (Join-Path $projectRoot "dependencies\basemodels\hubert_base.pt")) -and
    (Test-Path -LiteralPath (Join-Path $projectRoot "dependencies\basemodels\rmvpe.pt"))
}

function Merge-RuntimeZip($ZipPath) {
  # Extract to a short folder at the root of the install drive: same drive, so moving the
  # files afterwards is instant and needs no extra space on C:, and short, because some
  # PyTorch files are 140+ characters deep and Windows paths stop at 260.
  $tempRoot = Join-Path ([System.IO.Path]::GetPathRoot($projectRoot)) ("xxl-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
  try {
    New-Item -ItemType Directory -Force -Path $tempRoot -ErrorAction Stop | Out-Null
  } catch {
    $tempRoot = Join-Path $projectRoot ("xxl-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
  }
  try {
    Write-Host "Extracting runtime package (this takes a few minutes)..."
    Expand-Archive -Path $ZipPath -DestinationPath $tempRoot -Force
    $source = Get-ChildItem -Path $tempRoot -Directory -Recurse -Depth 2 -ErrorAction SilentlyContinue |
      Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "runtime\python.exe") } |
      Select-Object -First 1
    if (Test-Path -LiteralPath (Join-Path $tempRoot "runtime\python.exe")) { $sourcePath = $tempRoot }
    elseif ($source) { $sourcePath = $source.FullName }
    else { throw "The package does not contain runtime\python.exe." }

    foreach ($child in Get-ChildItem -LiteralPath $sourcePath -Force) {
      $dest = Join-Path $projectRoot $child.Name
      if (Test-Path -LiteralPath $dest) {
        if ($child.PSIsContainer) {
          # merge folder contents (e.g. Assets\Models) without overwriting user files
          & robocopy $child.FullName $dest /E /MOVE /XC /XN /XO /NFL /NDL /NJH /NJS /NP | Out-Null
          if ($LASTEXITCODE -ge 8) { throw "Runtime file merge failed: robocopy exit $LASTEXITCODE" }
        }
      } else {
        Move-Item -LiteralPath $child.FullName -Destination $dest
      }
    }
    foreach ($rel in $legacyItems) {
      $path = Join-Path $projectRoot $rel
      if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue }
    }
  } finally {
    if (Test-Path -LiteralPath $tempRoot) { Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue }
  }
}

function Ensure-Runtime {
  Write-Step 1 "Runtime"
  Write-Host "Install folder: $projectRoot"
  if (Test-RuntimeReady) {
    Write-Host "Runtime is ready."
    return
  }
  foreach ($zip in @((Join-Path $projectRoot "XiuxiulianRuntime.zip"), (Join-Path $projectRoot "VoiceChanger.zip"))) {
    if (Test-Path -LiteralPath $zip) {
      Write-Host "Runtime package: $zip"
      Merge-RuntimeZip $zip
      if (Test-RuntimeReady) { return }
    }
  }
  if ($VoiceChangerZipUrl) {
    $downloadDir = Join-Path $projectRoot ".xiuxiulian-downloads"
    $downloadPath = Join-Path $downloadDir "VoiceChanger.zip"
    Invoke-SetupDownload $VoiceChangerZipUrl $downloadPath
    Merge-RuntimeZip $downloadPath
    Remove-Item -LiteralPath $downloadDir -Recurse -Force -ErrorAction SilentlyContinue
  } else {
    $zip = Download-BackendParts
    if (!$zip) { throw "Runtime package is not configured." }
    Merge-RuntimeZip $zip
    Remove-Item -LiteralPath (Split-Path $zip -Parent) -Recurse -Force -ErrorAction SilentlyContinue
  }
  if (!(Test-RuntimeReady)) { throw "Runtime is still incomplete after extraction." }
}

function Get-StandardCableInstaller {
  # Only the original standard VB-CABLE archive is eligible for new installations.
  # Never pick an installer from the legacy configuration folder (it may be Cable A/B).
  $archive = Join-Path $projectRoot "drivers\VBCABLE_Driver_Pack46.zip"
  $expectedHash = "BC44E8974191A5A2427D6C979E2DAB08DBB09925F3F435DFA2F0BA3E45B6EB90"
  if (!(Test-Path -LiteralPath $archive)) {
    Invoke-SetupDownload "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack46.zip" $archive
  }
  if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expectedHash) {
    throw "VB-CABLE archive checksum failed. Remove drivers\VBCABLE_Driver_Pack46.zip and rerun setup."
  }
  $destination = Join-Path $projectRoot "drivers\vbcable-standard"
  Expand-Archive -LiteralPath $archive -DestinationPath $destination -Force
  $installer = Join-Path $destination "VBCABLE_Setup_x64.exe"
  if (!(Test-Path -LiteralPath $installer)) { throw "Standard VB-CABLE installer is missing." }
  return $installer
}

function Get-VirtualAudioDevices {
  $pattern = "VB-Audio|VB-Cable|CABLE Input|CABLE Output|Cable A|Cable B|伴侣扬声器|伴侣麦克风|VoiceMeeter"
  return @(Get-CimInstance Win32_PnPEntity | Where-Object {
    $_.Status -eq "OK" -and ($_.PNPClass -eq "AudioEndpoint" -or $_.PNPClass -eq "MEDIA") -and $_.Name -match $pattern
  })
}

function Ensure-VirtualAudio {
  Write-Step 2 "Virtual audio driver"
  if (@(Get-VirtualAudioDevices).Count -gt 0) {
    Write-Host "Virtual audio driver is installed; keeping the existing driver."
    return
  }
  Write-Host "VB-CABLE by VB-Audio Software: https://vb-audio.com/Cable/"
  Write-Host "VB-CABLE is donationware. Please support it if useful: https://shop.vb-audio.com/"
  $installer = Get-StandardCableInstaller
  Write-Host "Starting standard VB-CABLE setup. Approve the Windows administrator prompt, then click Install Driver."
  Start-Process -FilePath $installer -Verb RunAs -Wait
  if (@(Get-VirtualAudioDevices).Count -eq 0) {
    throw "VB-CABLE is not detected yet. Restart Windows and rerun first-time setup. If installation was cancelled, install the driver first."
  }
  Write-Host "VB-CABLE detected. Restart Windows before first use if the device is unavailable."
}

function Test-Requirements {
  Write-Step 0 "Check this computer"
  Write-Host "Install folder: $projectRoot"
  if ($projectRoot.Length -gt 80) {
    throw "Install path is too long. Move the program to a short path such as E:\xxl and retry."
  }
  try {
    $gpus = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name })
    Write-Host ("Graphics: " + ($gpus -join ", "))
    if (!($gpus | Where-Object { $_ -match "NVIDIA" })) {
      Write-Host "提醒：没有找到 NVIDIA 显卡。羞羞脸变声需要 NVIDIA 显卡（推荐 RTX 20 系列或更新）才能实时变声，没有的话会非常卡。" -ForegroundColor Yellow
    }
  } catch { }
  if (!(Test-RuntimeReady)) {
    $drive = (Get-Item -LiteralPath $projectRoot).PSDrive
    $freeGb = [math]::Round($drive.Free / 1GB, 1)
    Write-Host "Free space on $($drive.Name): $freeGb GB"
    if ($drive.Free -lt 16GB) {
      throw "Not enough free space ($freeGb GB). At least 16 GB is required during installation."
    }
  }
}

function New-DesktopShortcut {
  try {
    $exe = Join-Path $projectRoot "羞羞脸变声.exe"
    $desktop = [Environment]::GetFolderPath("Desktop")
    $link = Join-Path $desktop "羞羞脸变声.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($link)
    $shortcut.TargetPath = $exe
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.IconLocation = "$exe,0"
    $shortcut.Description = "羞羞脸变声"
    $shortcut.Save()
    Write-Host "Desktop shortcut: $link"
  } catch {
    Write-Host "Could not create the desktop shortcut: $($_.Exception.Message)"
  }
}

Write-Host "Setup version: $setupVersion"
Test-Requirements
Ensure-Runtime
Ensure-VirtualAudio
Write-Step 3 "Start"
New-DesktopShortcut
$exe = Join-Path $projectRoot "羞羞脸变声.exe"
Write-Host ""
Write-Host "安装完成！以后直接双击桌面上的「羞羞脸变声」就能用。" -ForegroundColor Green
Write-Host "如果刚装了虚拟声卡、软件里找不到它，重启一次电脑。"
Write-Host "Setup completed: $exe"
if ($StartApp) { Start-Process -FilePath $exe -WorkingDirectory $projectRoot }
