# TLI Autoloot

Tray tool for Torchlight Infinite:

- Spams Q (every 0.1s) while the game is focused and no menu is open. Menus are
  detected by the flame icon in the top-right HUD disappearing.
- Auto chest/mechanics: presses D while the grey finger icon above the D slot
  is on screen, except while the DPS meter panel is open.
- Spin-off **TLI Autoloot A** adds IRIS 2 auto blink. When the skill's yellow
  charge badge (in any skill slot) shows 0, Blink (A) is pressed according to
  the mode:
  - `+` presses Blink automatically
  - `=` presses Blink on each right click
  - `-` is off
- Options panel (left-click the tray icon): rebind Pause, Spam, Loot and Blink,
  toggle chests & mechanics, and pick the IRIS 2 auto blink mode.

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
