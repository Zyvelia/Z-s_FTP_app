# Run from an installed copy of Z FTP with Windows PowerShell 5.1 or newer.
param([switch]$CheckOnly)

$ErrorActionPreference = 'Stop'
$repo = 'Zyvelia/Z-s_FTP_app'
$versionFile = Join-Path $PSScriptRoot 'zftp-version.ini'
$application = Join-Path $PSScriptRoot 'ZFTP.exe'
$tempDirectory = $null

try {
    if (!(Test-Path -LiteralPath $application -PathType Leaf) -or
        !(Test-Path -LiteralPath $versionFile -PathType Leaf)) {
        throw 'Install Z FTP with the Windows installer before using this updater.'
    }

    $versionLine = Get-Content -LiteralPath $versionFile |
        Select-String '^Version=([0-9]+\.[0-9]+\.[0-9]+)$' |
        Select-Object -First 1
    if (!$versionLine) {
        throw 'The installed version could not be read. Reinstall Z FTP and try again.'
    }
    $installedVersion = [version]($versionLine.Matches[0].Groups[1].Value)

    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    try {
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$repo/releases/latest" `
            -Headers @{ Accept = 'application/vnd.github+json' } -UseBasicParsing -TimeoutSec 30
    } catch {
        if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 404) {
            Write-Host 'No stable Z FTP release is available on GitHub yet.'
            return
        }
        throw
    }

    if ($release.tag_name -cnotmatch '^v([0-9]+\.[0-9]+\.[0-9]+)$') {
        throw 'GitHub returned an unsupported release version.'
    }
    $latestVersion = [version]$Matches[1]
    if ($latestVersion -le $installedVersion) {
        Write-Host "Z FTP $installedVersion is up to date."
        return
    }

    $name = "ZFTP_Setup_$($release.tag_name).exe"
    $url = "https://github.com/$repo/releases/download/$($release.tag_name)/$name"
    $asset = $release.assets | Where-Object {
        $_.name -ceq $name -and $_.browser_download_url -ceq $url -and $_.state -eq 'uploaded'
    } | Select-Object -First 1
    if (!$asset -or $asset.digest -notmatch '^sha256:([a-f0-9]{64})$') {
        throw "The release does not have a verifiable Windows installer named $name."
    }
    $expectedHash = $Matches[1]
    Write-Host "Z FTP $latestVersion is available. Installed version $installedVersion."
    if ($CheckOnly) { return }

    if (Get-Process -Name ZFTP -ErrorAction SilentlyContinue) {
        throw 'Quit Z FTP from the window or tray, then run the updater again.'
    }

    $tempDirectory = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
    New-Item -ItemType Directory -Path $tempDirectory | Out-Null
    $installer = Join-Path $tempDirectory $name
    Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing -TimeoutSec 600
    if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash -ne $expectedHash) {
        throw 'The downloaded installer failed SHA-256 verification.'
    }

    if (Get-Process -Name ZFTP -ErrorAction SilentlyContinue) {
        throw 'Quit Z FTP from the window or tray, then run the updater again.'
    }
    $process = Start-Process -FilePath $installer `
        -ArgumentList @("/DIR=`"$PSScriptRoot`"", '/NOCLOSEAPPLICATIONS') -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Setup did not complete (exit code $($process.ExitCode))."
    }
    Write-Host "Z FTP $latestVersion was installed."
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
} finally {
    if ($tempDirectory) {
        Remove-Item -LiteralPath $tempDirectory -Recurse -Force -ErrorAction SilentlyContinue
    }
}
