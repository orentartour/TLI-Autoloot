# Builds TLI-Autoloot.exe and puts it on the desktop. Quit the app first.
$d = $PSScriptRoot
$work = Join-Path $env:TEMP "tli_autoloot_build"
py -3.13 -m PyInstaller --noconfirm --onefile --noconsole --name TLI-Autoloot --icon "$d\app.ico" `
    --add-data "$d\templates;templates" --add-data "$d\fe.png;." `
    --exclude-module numpy --exclude-module PIL._avif --exclude-module PIL._webp `
    --exclude-module PIL._imagingft --exclude-module PIL._imagingcms `
    --exclude-module ssl --exclude-module _ssl --exclude-module _hashlib `
    --distpath ([Environment]::GetFolderPath('Desktop')) --workpath $work --specpath $work "$d\tli_autoloot.py"
Remove-Item -Recurse -Force $work
