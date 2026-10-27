# TLI Autoloot

Tray tool for Torchlight Infinite:

- Spams Q (every 0.1s) while the game is focused and no menu is open. Menus are
  detected by the flame icon in the top-right HUD disappearing.
- Auto chest/mechanics: presses D while the grey finger icon is on screen.
- Options window (left-click the tray icon, or right-click > Options): rebind the pause, spam and auto
  keys, and turn auto chest/mechanics on or off.

## Hotkeys

| Key | Action |
| --- | --- |
| F8 (rebindable) | Pause / resume everything |
| Ctrl+F12 | Capture the finger icon (hover it first) |
| Ctrl+F11 | Recapture the flame HUD icon |
| Ctrl+F9 / Ctrl+F10 | Add / clear extra menu spots |

## Build

```powershell
.\build.ps1   # needs Python 3.13 with pystray, Pillow and PyInstaller
```

The game runs elevated, so the app needs admin too. On first launch it asks
once and registers a "TLI Autoloot" scheduled task to start elevated afterwards.
Settings and templates live in `%APPDATA%\TLIQSpam`.
