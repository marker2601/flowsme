# Removes FlowSME (or FlowSME Recorder: product.json says which) for the current Windows user.
# Recordings in %LOCALAPPDATA%\<data> are kept.
$dest = Split-Path -Parent $MyInvocation.MyCommand.Path
$product = @{ name = "FlowSME"; exe = "FlowSME.exe"; folder = "FlowSME"; data = "FlowSME" }
$file = Join-Path $dest "product.json"
if (Test-Path $file) { (Get-Content $file -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $product[$_.Name] = $_.Value } }
$name = $product.name
$data = Join-Path $env:LOCALAPPDATA $product.data
$shell = New-Object -ComObject WScript.Shell
$quiet = $args -contains "/quiet"

if (Get-Process ([IO.Path]::GetFileNameWithoutExtension($product.exe)) -ErrorAction SilentlyContinue) {
    if (-not $quiet) { $null = $shell.Popup("Close $name first, then uninstall it.", 0, $name, 0x30) }
    exit 1
}
if (-not $quiet -and $shell.Popup("Remove $name from this computer?`n`nYour recordings stay in $data.", 0,
                                  $name, 0x24) -ne 6) { exit 0 }

foreach ($folder in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"))) {
    Remove-Item (Join-Path $folder "$name.lnk") -ErrorAction SilentlyContinue
}
Remove-Item "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$($product.folder)" -Recurse -ErrorAction SilentlyContinue
Set-Location $env:TEMP  # leave the folder before deleting it
Remove-Item $dest -Recurse -Force -ErrorAction SilentlyContinue
if (-not $quiet) {
    $null = $shell.Popup("$name was removed. Your recordings are still in $data; delete that folder to remove " +
                         "them too.", 0, $name, 0x40)
}
