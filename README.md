# TLI Autoloot

Tray tool for Torchlight Infinite:

- Spams Q (every 0.1s) while the game is focused and no menu is open. Menus are
  detected by the flame icon in the top-right HUD disappearing.
- Auto chest/mechanics: presses D while the grey finger icon above the D slot
  is on screen (it always appears at the same spot).
- Spin-off **TLI Autoloot A**: everything above, plus it presses A while the
  skill in slot A shows 0 charges on its yellow badge.
- Options window (left-click the tray icon, or right-click > Options): rebind the pause, spam and auto
  keys, and turn auto chest/mechanics on or off.

## Hotkeys

| Key | Action |
| --- | --- |
| F8 (rebindable) | Pause / resume everything |
| Ctrl+F11 | Recapture the flame HUD icon |
| Ctrl+F9 / Ctrl+F10 | Add / clear extra menu spots |

## Build

```powershell
.\build.ps1      # TLI Autoloot
.\build.ps1 -A   # the TLI Autoloot A spin-off
# needs Python 3.13 with pystray, Pillow and PyInstaller
```

The game runs elevated, so the app needs admin too. On first launch it asks
once and registers a "TLI Autoloot" scheduled task to start elevated afterwards.
Settings and templates live in `%APPDATA%\TLIQSpam`.
