<#
.SYNOPSIS
  Tags every talkgroup alias in your SDRTrunk playlist so its calls are
  streamed to Wake Radio (Rdio Scanner).

.DESCRIPTION
  SDRTrunk only uploads a call if the talkgroup's alias is assigned to the
  stream. Clicking through 326 aliases by hand is tedious, so this adds the
  stream to all of them in one pass.

  - Quit SDRTrunk first (it rewrites the playlist when it exits).
  - A timestamped backup of the playlist is saved next to it.
  - Aliases already tagged are left alone, so it is safe to re-run after
    you add new aliases.

.PARAMETER StreamName
  The name you gave the Rdio Scanner stream in SDRTrunk's Streaming tab.

.PARAMETER Playlist
  Path to the playlist. Defaults to SDRTrunk's default playlist.

.PARAMETER WhatIf
  Report what would change without writing anything.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\Add-RdioStreamToAliases.ps1
#>
[CmdletBinding()]
param(
    [string]$StreamName = "wakeradio",
    [string]$Playlist = (Join-Path $env:USERPROFILE "SDRTrunk\playlist\default.xml"),
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $Playlist)) {
    throw "Playlist not found: $Playlist"
}

# SDRTrunk runs as java/javaw; refuse to edit while it is open.
$running = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^javaw?\.exe$' -and $_.CommandLine -match 'sdrtrunk' }
if ($running -and -not $WhatIf) {
    throw "SDRTrunk is running. Quit it (File > Exit) and run this again."
}

$doc = New-Object System.Xml.XmlDocument
$doc.PreserveWhitespace = $true
$doc.Load((Resolve-Path -LiteralPath $Playlist).Path)

# The stream must exist in the playlist (created in the Streaming tab).
$streamFound = $doc.SelectNodes("//*[@name='$StreamName']") |
    Where-Object { $_.LocalName -ne 'alias' }
if (-not $streamFound) {
    Write-Warning ("No stream named '$StreamName' found in the playlist. Create it in SDRTrunk's " +
        "Streaming tab first (or pass -StreamName with the name you used). Aliases will still be tagged.")
}

$talkgroupTypes = @('talkgroup', 'talkgroupRange', 'p25FullyQualifiedTalkgroup', 'talkgroupID')
$tagged = 0; $already = 0; $skipped = 0

foreach ($alias in $doc.SelectNodes("//alias")) {
    $ids = @($alias.SelectNodes("id"))
    $isTalkgroup = $ids | Where-Object { $talkgroupTypes -contains $_.GetAttribute("type") }
    if (-not $isTalkgroup) { $skipped++; continue }

    $has = $ids | Where-Object {
        $_.GetAttribute("type") -eq "broadcastChannel" -and $_.GetAttribute("channel") -eq $StreamName
    }
    if ($has) { $already++; continue }

    $id = $doc.CreateElement("id")
    $id.SetAttribute("type", "broadcastChannel")
    $id.SetAttribute("channel", $StreamName)

    # Match the indentation of the alias's existing ids so the file stays tidy.
    $last = $ids[-1]
    $indent = if ($last.PreviousSibling -and $last.PreviousSibling.NodeType -eq 'Whitespace') {
        $last.PreviousSibling.Value } else { "`n    " }
    $alias.InsertAfter($id, $last) | Out-Null
    $alias.InsertAfter($doc.CreateWhitespace($indent), $last) | Out-Null
    $tagged++
}

Write-Host ("Talkgroup aliases: {0} newly tagged, {1} already tagged. Non-talkgroup aliases left alone: {2}." -f `
    $tagged, $already, $skipped)

if ($WhatIf) { Write-Host "WhatIf: nothing written."; return }
if ($tagged -eq 0) { Write-Host "Nothing to change."; return }

$backup = "$Playlist.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
Copy-Item -LiteralPath $Playlist -Destination $backup
Write-Host "Backup saved: $backup"

$settings = New-Object System.Xml.XmlWriterSettings
$settings.Encoding = New-Object System.Text.UTF8Encoding($false)   # no BOM
$writer = [System.Xml.XmlWriter]::Create($Playlist, $settings)
try { $doc.Save($writer) } finally { $writer.Close() }

Write-Host "Done. Start SDRTrunk; calls on those talkgroups will now stream to '$StreamName'."
