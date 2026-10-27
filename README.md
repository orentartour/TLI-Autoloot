# TLI Autoloot

A small tray helper for Torchlight Infinite. While the game is the active
window it:

- **Spams Q** every 0.1s. It stops while a menu is open, which it detects when
  the flame icon in the top-right HUD disappears.
- **Auto loots:** presses D while the grey finger icon above the D slot is on
  screen, but not while the DPS meter panel is open.

**TLI Autoloot A** is a spin-off that adds **IRIS 2 auto blink**. When a skill's
yellow charge badge reads 0, it presses Blink (A):

| Mode | Blink |
| --- | --- |
| `+` | pressed automatically |
| `=` | pressed on each right click |
| `-` | off |

## Download

Get `TLI-Autoloot.exe` or `TLI-Autoloot-A.exe` from the
[latest release](https://github.com/orentartour/TLI-Autoloot/releases/latest)
and run it. Run only one of the two at a time.

The game runs as admin, so the app needs admin too. The first launch asks once
and sets up a scheduled task, so later launches don't show a prompt.

## Hotkey

**F8** pauses and resumes everything. You can rebind it in Options.

## Options

<img src="docs/options.png" alt="The Options panel of TLI Autoloot A" width="280" align="right">

Left-click the tray icon to open the Options panel. From there you can:

- rebind Pause / resume (F8 by default), Spam, Loot and Blink: click a key,
  then press the new one (Esc cancels)
- switch chests & mechanics looting on or off
- pick the IRIS 2 auto blink mode (spin-off only)

The default keys are the AZERTY positions of Q and A. On QWERTY, rebind Spam
and Blink once.

Settings are saved in `%APPDATA%\TLI Autoloot`.

## Build

```powershell
.\build.ps1      # TLI-Autoloot.exe
.\build.ps1 -A   # TLI-Autoloot-A.exe
```

You need Python 3.13 with pystray, Pillow and PyInstaller. Screen positions
assume the game's UI at 2560x1440; other resolutions are scaled by window
height.
