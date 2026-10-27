# Builds TLI-Autoloot.exe (or, with -A, the spin-off TLI-Autoloot-A.exe)
# and puts it on the desktop. Quit the app first.
param([switch]$A)
$d = $PSScriptRoot
$name, $entry = if ($A) { "TLI-Autoloot-A", "tli_autoloot_a.py" } else { "TLI-Autoloot", "tli_autoloot.py" }
$work = Join-Path $env:TEMP "tli_autoloot_build"
py -3.13 -m PyInstaller --noconfirm --onefile --noconsole --name $name --icon "$d\app.ico" `
    --add-data "$d\templates;templates" --add-data "$d\fe.png;." `
    --exclude-module numpy --exclude-module PIL._avif --exclude-module PIL._webp `
    --exclude-module PIL._imagingft --exclude-module PIL._imagingcms `
    --exclude-module ssl --exclude-module _ssl --exclude-module _hashlib `
    --distpath ([Environment]::GetFolderPath('Desktop')) --workpath $work --specpath $work "$d\$entry"
Remove-Item -Recurse -Force $work
