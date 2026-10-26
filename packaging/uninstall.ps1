# Removes FlowSME for the current Windows user. Recordings in %LOCALAPPDATA%\FlowSME are kept.
$dest = Split-Path -Parent $MyInvocation.MyCommand.Path
$data = Join-Path $env:LOCALAPPDATA "FlowSME"
$shell = New-Object -ComObject WScript.Shell
$quiet = $args -contains "/quiet"

if (Get-Process FlowSME -ErrorAction SilentlyContinue) {
    if (-not $quiet) { $null = $shell.Popup("Close FlowSME first, then uninstall it.", 0, "FlowSME", 0x30) }
    exit 1
}
if (-not $quiet -and $shell.Popup("Remove FlowSME from this computer?`n`nYour recordings stay in $data.", 0,
                                  "FlowSME", 0x24) -ne 6) { exit 0 }

foreach ($folder in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"))) {
    Remove-Item (Join-Path $folder "FlowSME.lnk") -ErrorAction SilentlyContinue
}
Remove-Item "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\FlowSME" -Recurse -ErrorAction SilentlyContinue
Set-Location $env:TEMP  # leave the folder before deleting it
Remove-Item $dest -Recurse -Force -ErrorAction SilentlyContinue
if (-not $quiet) {
    $null = $shell.Popup("FlowSME was removed. Your recordings are still in $data; delete that folder to remove " +
                         "them too.", 0, "FlowSME", 0x40)
}
