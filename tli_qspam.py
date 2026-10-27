"""TLI Autoloot: Torchlight Infinite Q spammer with auto chest/mechanics.

Presses Q every 0.1s while Torchlight Infinite is the active window and the
flame icon in the top-right corner of the HUD is visible. When a menu opens
and that icon disappears, it stops.

The icon is matched against a reference image in templates/. If detection
ever drifts (new resolution, UI scale), stand in game with the icon visible
and press Ctrl+F11 to recapture it.

Optional extra menu spots, for menus that leave the icon visible:
  open the menu, hover a distinctive static spot of it, press Ctrl+F9.
  Ctrl+F10 clears those spots.
F8 pauses / resumes spamming at any time.

Auto chest/mechanics: while spamming is on (not paused, game focused) and the
grey finger icon is showing, presses D. Hover the icon and press Ctrl+F12 to
capture it. Keys and the on/off switch are in the Options window (left-click
the tray icon).

Tray icon colour: green = spamming, orange = menu open,
grey = game not focused, red = paused.
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import winsound

import pystray
from PIL import Image, ImageChops, ImageDraw, ImageGrab, ImageStat, ImageTk

HERE = os.path.dirname(os.path.abspath(__file__))
# Built as an exe: bundled files are unpacked to a temp dir, so settings and
# templates live in %APPDATA% and the bundled templates seed them on first run.
BUNDLE_DIR = getattr(sys, "_MEIPASS", HERE)
if getattr(sys, "frozen", False):
    DATA_DIR = os.path.join(os.environ["APPDATA"], "TLIQSpam")
else:
    DATA_DIR = HERE
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
LOG_PATH = os.path.join(DATA_DIR, "autoloot.log")
TASK_NAME = "TLI Autoloot"
TEMPLATE_DIR = os.path.join(DATA_DIR, "templates")

DEFAULT_CONFIG = {
    "process_name": "torchlight_infinite.exe",
    "interval_seconds": 0.1,
    "key_scancode": 0x1E,  # physical key that types Q on AZERTY
    "color_tolerance": 20,
    "menu_points": [],
    # Flame icon in the top-right HUD, in client pixels of a 2560x1440 window.
    # It is anchored to the right edge, so x is measured from that edge.
    "hud_boxes": [[2476, 20, 2528, 74]],
    "hud_reference_size": [2560, 1440],
    "hud_match_threshold": 0.45,
    "pause_vk": 0x77,  # F8
    "auto_enabled": True,
    "auto_scancode": 0x20,  # D
    "auto_interval_seconds": 0.3,
    # Finger icon centre, in pixels of a 1440-high window, measured from the
    # bottom centre of the window. Set with Ctrl+F12 while hovering it.
    "auto_center": [4, -322],
    # Max grey difference to the template, as a fraction of the template's own
    # contrast: ~0 is a perfect match, ~1 is a flat patch of its average colour.
    "auto_match_ratio": 0.5,
}

# The icon's outline is light yellow; its orange glow stays below this.
BRIGHTNESS = 170
# How far (px) the icon may shift from its saved position and still match.
SEARCH_MARGIN = 6
# Consecutive missed icon checks before calling it a menu (ignores flashes).
MISSES_FOR_MENU = 3
HUD_CHECK_INTERVAL = 0.05
# Half-size (px) of the square captured around the finger icon, and how far
# it may shift and still match.
AUTO_HALF = 20
AUTO_MARGIN = 4

# Offsets sampled around each extra menu spot; all must match.
SAMPLE_OFFSETS = [(0, 0), (-4, 0), (4, 0), (0, -4), (0, 4)]

VK_CONTROL, VK_ESCAPE, VK_F9, VK_F10, VK_F11, VK_F12 = 0x11, 0x1B, 0x78, 0x79, 0x7A, 0x7B
# Keys a pause bind can use: everything except mouse buttons.
BINDABLE_VKS = [vk for vk in range(0x08, 0xFF) if vk != VK_ESCAPE]
# Keys whose name lookup needs the "extended" flag (arrows, Insert, ...).
EXTENDED_VKS = set(range(0x21, 0x2F)) | {0x5B, 0x5C, 0x5D, 0x6F, 0x90}
WIN_VKS = {0x5B, 0x5C}

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Physical pixel coordinates, so positions work on scaled displays.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    user32.SetProcessDPIAware()

user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
gdi32.GetPixel.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.GetPixel.restype = wt.DWORD
user32.GetForegroundWindow.restype = wt.HWND
kernel32.OpenProcess.restype = wt.HANDLE


# --- SendInput -------------------------------------------------------------

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002


def send_scancode(scancode, up):
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    inp = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, scancode, flags, 0, 0))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def tap_key(scancode, hold=0.015):
    send_scancode(scancode, up=False)
    time.sleep(hold)
    send_scancode(scancode, up=True)


# --- Window / screen helpers -------------------------------------------------

def foreground_process_name():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, None
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return hwnd, None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return hwnd, None
        return hwnd, os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(handle)


def client_origin(hwnd):
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def client_size(hwnd):
    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    return rect.right - rect.left, rect.bottom - rect.top


def cursor_pos():
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def read_pixels(coords):
    hdc = user32.GetDC(None)
    try:
        out = []
        for x, y in coords:
            c = gdi32.GetPixel(hdc, x, y)
            out.append((c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF))
        return out
    finally:
        user32.ReleaseDC(None, hdc)


def key_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def key_name(vk):
    scan = user32.MapVirtualKeyW(vk, 0)
    lparam = (scan << 16) | ((1 << 24) if vk in EXTENDED_VKS else 0)
    buf = ctypes.create_unicode_buffer(64)
    if scan and user32.GetKeyNameTextW(lparam, buf, len(buf)):
        return buf.value
    return f"key 0x{vk:02X}"


def scancode_name(scan):
    buf = ctypes.create_unicode_buffer(64)
    if user32.GetKeyNameTextW(scan << 16, buf, len(buf)):
        return buf.value
    return f"scan 0x{scan:02X}"


# --- HUD icon matching ---------------------------------------------------------

def bright_mask(img):
    """Bright pixels as (width, rows): one int per row, bit width-1-x set
    when pixel x is bright."""
    bits = img.convert("L").point(lambda v: 255 if v > BRIGHTNESS else 0, "1")
    w, h = bits.size
    stride = (w + 7) // 8
    data = bits.tobytes()
    pad = stride * 8 - w
    return w, [int.from_bytes(data[y * stride:(y + 1) * stride], "big") >> pad
               for y in range(h)]


def best_overlap(ref, area):
    """Best intersection-over-union of ref slid across the larger area."""
    w, ref_rows = ref
    area_w, area_rows = area
    h = len(ref_rows)
    full = (1 << w) - 1
    ref_on = sum(r.bit_count() for r in ref_rows)
    best = 0.0
    for dx in range(area_w - w + 1):
        shift = area_w - w - dx
        cols = [(a >> shift) & full for a in area_rows]
        counts = [c.bit_count() for c in cols]
        for dy in range(len(area_rows) - h + 1):
            hits = sum((r & c).bit_count() for r, c in zip(ref_rows, cols[dy:dy + h]))
            union = ref_on + sum(counts[dy:dy + h]) - hits
            if union:
                best = max(best, hits / union)
    return best


def template_path(i):
    return os.path.join(TEMPLATE_DIR, f"hud_{i}.png")


def save_templates(source, boxes, offset=(0, 0)):
    """Crop each box out of source (a PIL image) and store it as a template."""
    os.makedirs(TEMPLATE_DIR, exist_ok=True)
    ox, oy = offset
    for i, (x0, y0, x1, y1) in enumerate(boxes):
        source.crop((x0 - ox, y0 - oy, x1 - ox, y1 - oy)).save(template_path(i))


AUTO_TEMPLATE = "auto.png"


def half_size(img):
    """Compared at half resolution: softer on anti-aliasing and 1px drift."""
    return img.resize((img.width // 2, img.height // 2), Image.BOX)


def load_auto_template():
    """The finger icon in grey (half size), with its mean deviation from its average."""
    path = os.path.join(TEMPLATE_DIR, AUTO_TEMPLATE)
    if not os.path.exists(path):
        bundled = os.path.join(BUNDLE_DIR, "templates", AUTO_TEMPLATE)
        if not os.path.exists(bundled):
            return None
        os.makedirs(TEMPLATE_DIR, exist_ok=True)
        shutil.copy(bundled, path)
    img = half_size(Image.open(path).convert("L"))
    flat = Image.new("L", img.size, round(ImageStat.Stat(img).mean[0]))
    return img, max(ImageStat.Stat(ImageChops.difference(img, flat)).mean[0], 1.0)


def load_templates(count):
    bundled = os.path.join(BUNDLE_DIR, "templates")
    if not os.path.isdir(TEMPLATE_DIR) and os.path.isdir(bundled):
        shutil.copytree(bundled, TEMPLATE_DIR)
    try:
        return [bright_mask(Image.open(template_path(i))) for i in range(count)]
    except FileNotFoundError:
        return None


# --- Config ------------------------------------------------------------------

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    save_config(cfg)
    return cfg


def save_config(cfg):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# --- Tray icons --------------------------------------------------------------

STATE_COLORS = {
    "spamming": (46, 204, 64),
    "menu": (255, 150, 0),
    "unfocused": (130, 130, 130),
    "paused": (220, 40, 40),
}
STATE_LABELS = {
    "spamming": "Spamming Q",
    "menu": "Menu open - idle",
    "unfocused": "Game not focused - idle",
    "paused": "Paused",
}


def make_icon(color, size=64):
    """Flame Elementium crystal with a status dot in the bottom-right corner."""
    img = Image.open(os.path.join(BUNDLE_DIR, "fe.png")).convert("RGBA").resize((size, size))
    d = ImageDraw.Draw(img)
    r = size * 0.42
    d.ellipse((size - r - 1, size - r - 1, size - 1, size - 1), fill=color,
              outline=(20, 20, 20), width=max(2, size // 20))
    return img


# --- Options window ------------------------------------------------------------

BG, PANEL, FG, DIM, ACCENT = "#1d1e23", "#2b2d34", "#ececee", "#9b9ca4", "#e8792b"
FONT = ("Segoe UI", 10)
BINDS = [("pause", "Pause / resume"), ("spam", "Spam key"), ("auto", "Auto chest/mechanics key")]


def hex_color(rgb):
    return "#%02x%02x%02x" % rgb


class OptionsWindow:
    """Small always-on-top window for the keys and the auto chest switch.
    Tk lives in its own thread and re-reads the spammer's state every 100ms."""

    def __init__(self, app):
        self.app = app
        self.root = None
        self.lock = threading.Lock()

    def show(self):
        with self.lock:
            if self.root:
                self.root.after(0, self.root.deiconify)
                self.root.after(0, self.root.lift)
                return
            self.root = True  # claimed; the thread replaces it with the window
        threading.Thread(target=self.build, daemon=True).start()

    def close(self):
        with self.lock:
            root, self.root = self.root, None
        root.destroy()

    def build(self):
        app = self.app
        root = tk.Tk()
        root.title("TLI Autoloot - Options")
        root.configure(bg=BG, padx=16, pady=14)
        root.attributes("-topmost", True)
        root.resizable(False, False)
        self.logo = ImageTk.PhotoImage(
            Image.open(os.path.join(BUNDLE_DIR, "fe.png")).resize((32, 32)), master=root)
        root.iconphoto(True, self.logo)
        root.protocol("WM_DELETE_WINDOW", self.close)

        status = tk.Label(root, font=("Segoe UI", 11, "bold"), bg=BG, fg=FG, anchor="w")
        status.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 10))

        # Buttons never take focus, so the key pressed to bind can't click them.
        buttons = {}
        for row, (which, label) in enumerate(BINDS, start=1):
            tk.Label(root, text=label, font=FONT, bg=BG, fg=FG, anchor="w").grid(
                row=row, column=0, sticky="w", pady=3)
            b = tk.Button(root, font=FONT, width=16, takefocus=0, relief="flat",
                          bg=PANEL, fg=FG, activebackground=ACCENT, activeforeground=BG,
                          command=lambda w=which: app.start_rebind(w))
            b.grid(row=row, column=1, sticky="e", padx=(16, 0), pady=3)
            buttons[which] = b

        auto_var = tk.BooleanVar(master=root)
        tk.Checkbutton(root, text="Auto chest/mechanics", variable=auto_var, font=FONT,
                       takefocus=0, bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG,
                       activeforeground=FG, command=app.toggle_auto).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(12, 2))

        finger = tk.Frame(root, bg=PANEL, padx=10, pady=8)
        finger.grid(row=5, column=0, columnspan=2, sticky="we", pady=(4, 0))
        thumb = tk.Label(finger, bg=PANEL)
        thumb.pack(side="left")
        finger_text = tk.Label(finger, font=("Segoe UI", 9), bg=PANEL, fg=DIM,
                               justify="left", anchor="w")
        finger_text.pack(side="left", padx=(10, 0))

        live = tk.Label(root, font=("Segoe UI", 9), bg=BG, fg=DIM, anchor="w")
        live.grid(row=6, column=0, columnspan=2, sticky="we", pady=(6, 0))

        clear = tk.Button(root, font=("Segoe UI", 8), takefocus=0, relief="flat",
                          bg=PANEL, fg=DIM, activebackground=ACCENT, activeforeground=BG,
                          command=app.clear_menu_points)
        tk.Label(root, text="Esc cancels a key change.", font=("Segoe UI", 8),
                 bg=BG, fg=DIM).grid(row=7, column=0, sticky="w", pady=(8, 0))

        names = {"pause": lambda: key_name(app.cfg["pause_vk"]),
                 "spam": lambda: scancode_name(app.cfg["key_scancode"]),
                 "auto": lambda: scancode_name(app.cfg["auto_scancode"])}
        shown = {"template": False}

        def refresh():
            if self.root is not root:
                return
            state = app.state or "unfocused"
            status.config(text=f"\u25cf  {STATE_LABELS[state]}",
                          fg=hex_color(STATE_COLORS[state]))
            for which, b in buttons.items():
                waiting = app.rebinding == which
                b.config(text="Press a key..." if waiting else names[which](),
                         bg=ACCENT if waiting else PANEL, fg=BG if waiting else FG)
            auto_var.set(app.cfg["auto_enabled"])
            if app.auto_score is None:
                live.config(text=f"Finger icon: not checking (spam off)   "
                                 f"{names['auto']()} presses: {app.auto_presses}", fg=DIM)
            else:
                live.config(text=f"Finger icon: {'SEEN' if app.auto_seen else 'not seen'} "
                                 f"(score {app.auto_score:.2f})   "
                                 f"{names['auto']()} presses: {app.auto_presses}",
                            fg=ACCENT if app.auto_seen else DIM)
            spots = len(app.cfg["menu_points"])
            if spots:
                clear.config(text=f"Clear extra menu spots ({spots})")
                clear.grid(row=7, column=1, sticky="e", pady=(8, 0))
            else:
                clear.grid_remove()
            if app.auto_template is not shown["template"]:
                shown["template"] = app.auto_template
                if app.auto_template:
                    self.thumb = ImageTk.PhotoImage(
                        app.auto_template[0].resize((40, 40)), master=root)
                    thumb.config(image=self.thumb, width=40)
                    finger_text.config(text="Finger icon captured.\n"
                                            "Hover it + Ctrl+F12 to recapture.")
                else:
                    thumb.config(image="", width=0)
                    finger_text.config(text="Finger icon not captured yet.\n"
                                            "In game, hover it + Ctrl+F12.")
            root.after(100, refresh)

        with self.lock:
            self.root = root
        refresh()
        root.mainloop()


# --- Main logic ----------------------------------------------------------------

class Spammer:
    def __init__(self):
        self.cfg = load_config()
        self.templates = load_templates(len(self.cfg["hud_boxes"]))
        self.paused = False
        self.rebinding = None  # "pause", "spam" or "auto" while waiting for a key
        self.auto_template = load_auto_template()
        self.next_auto = 0.0
        self.auto_score = None
        self.auto_seen = False
        self.auto_presses = 0
        self.options = OptionsWindow(self)
        self.running = True
        self.state = None
        self.icon = None
        self.icons = {s: make_icon(c) for s, c in STATE_COLORS.items()}
        self.hud_misses = MISSES_FOR_MENU
        self.next_hud_check = 0.0

    def scaled_boxes(self, hwnd):
        """HUD boxes in screen coordinates for the current window size."""
        ref_w, ref_h = self.cfg["hud_reference_size"]
        w, h = client_size(hwnd)
        s = h / ref_h  # the UI scales with window height
        ox, oy = client_origin(hwnd)
        right = ox + w
        return [[round(right - (ref_w - x0) * s), round(oy + y0 * s),
                 round(right - (ref_w - x1) * s), round(oy + y1 * s)]
                for x0, y0, x1, y1 in self.cfg["hud_boxes"]]

    def hud_visible(self, hwnd):
        if not self.templates:
            return True  # nothing to compare against: don't block spamming
        boxes = self.scaled_boxes(hwnd)
        m = SEARCH_MARGIN
        left = min(b[0] for b in boxes) - m
        top = min(b[1] for b in boxes) - m
        right = max(b[2] for b in boxes) + m
        bottom = max(b[3] for b in boxes) + m
        shot = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
        scores = []
        for ref, (x0, y0, x1, y1) in zip(self.templates, boxes):
            sub = bright_mask(shot.crop((x0 - left - m, y0 - top - m,
                                         x1 - left + m, y1 - top + m)))
            if sub[0] < ref[0] or len(sub[1]) < len(ref[1]):
                return True  # window size changed since capture; Ctrl+F11 fixes it
            scores.append(best_overlap(ref, sub))
        return sum(scores) / len(scores) >= self.cfg["hud_match_threshold"]

    def menu_spot_matches(self, hwnd):
        ox, oy = client_origin(hwnd)
        tol = self.cfg["color_tolerance"]
        for p in self.cfg["menu_points"]:
            coords = [(ox + p["x"] + dx, oy + p["y"] + dy) for dx, dy in SAMPLE_OFFSETS]
            live = read_pixels(coords)
            if all(max(abs(a - b) for a, b in zip(px, ref)) <= tol
                   for px, ref in zip(live, p["colors"])):
                return True
        return False

    def menu_open(self, hwnd):
        now = time.perf_counter()
        if now >= self.next_hud_check:
            self.next_hud_check = now + HUD_CHECK_INTERVAL
            self.hud_misses = 0 if self.hud_visible(hwnd) else self.hud_misses + 1
        return self.hud_misses >= MISSES_FOR_MENU or self.menu_spot_matches(hwnd)

    def game_window(self):
        hwnd, name = foreground_process_name()
        if name != self.cfg["process_name"]:
            winsound.Beep(300, 250)
            return None
        return hwnd

    def recapture_hud(self):
        hwnd = self.game_window()
        if not hwnd:
            return
        boxes = self.scaled_boxes(hwnd)
        left = min(b[0] for b in boxes)
        top = min(b[1] for b in boxes)
        shot = ImageGrab.grab(bbox=(left, top, max(b[2] for b in boxes),
                                    max(b[3] for b in boxes)), all_screens=True)
        save_templates(shot, boxes, offset=(left, top))
        self.templates = load_templates(len(boxes))
        winsound.Beep(1200, 120)
        winsound.Beep(1500, 120)

    def add_menu_point(self):
        hwnd = self.game_window()
        if not hwnd:
            return
        cx, cy = cursor_pos()
        ox, oy = client_origin(hwnd)
        colors = read_pixels([(cx + dx, cy + dy) for dx, dy in SAMPLE_OFFSETS])
        self.cfg["menu_points"].append(
            {"x": cx - ox, "y": cy - oy, "colors": [list(c) for c in colors]})
        save_config(self.cfg)
        winsound.Beep(1200, 120)

    def clear_menu_points(self):
        self.cfg["menu_points"] = []
        save_config(self.cfg)
        winsound.Beep(600, 120)
        winsound.Beep(400, 120)

    def auto_box(self, hwnd):
        """Screen box around the finger icon for the current window size."""
        w, h = client_size(hwnd)
        s = h / self.cfg["hud_reference_size"][1]
        ox, oy = client_origin(hwnd)
        dx, dy = self.cfg["auto_center"]
        cx, cy = round(ox + w / 2 + dx * s), round(oy + h + dy * s)
        return cx - AUTO_HALF, cy - AUTO_HALF, cx + AUTO_HALF, cy + AUTO_HALF

    def capture_auto_icon(self):
        hwnd = self.game_window()
        if not hwnd:
            return
        cx, cy = cursor_pos()
        w, h = client_size(hwnd)
        s = h / self.cfg["hud_reference_size"][1]
        ox, oy = client_origin(hwnd)
        self.cfg["auto_center"] = [(cx - ox - w / 2) / s, (cy - oy - h) / s]
        save_config(self.cfg)
        os.makedirs(TEMPLATE_DIR, exist_ok=True)
        ImageGrab.grab(bbox=self.auto_box(hwnd), all_screens=True).save(
            os.path.join(TEMPLATE_DIR, AUTO_TEMPLATE))
        self.auto_template = load_auto_template()
        self.icon.update_menu()
        winsound.Beep(1200, 120)
        winsound.Beep(1500, 120)

    def auto_icon_score(self, hwnd):
        """Best difference to the finger icon, relative to its contrast (0 = exact)."""
        if self.auto_template is None or not self.cfg["auto_center"]:
            return None
        x0, y0, x1, y1 = self.auto_box(hwnd)
        m = AUTO_MARGIN
        area = half_size(ImageGrab.grab(bbox=(x0 - m, y0 - m, x1 + m, y1 + m),
                                        all_screens=True).convert("L"))
        tpl, contrast = self.auto_template
        tw, th = tpl.size
        return min(ImageStat.Stat(ImageChops.difference(
                       area.crop((dx, dy, dx + tw, dy + th)), tpl)).mean[0]
                   for dx in range(m + 1) for dy in range(m + 1)) / contrast

    def log(self, text):
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {text}\n")
        except OSError:
            pass

    def toggle_auto(self):
        self.cfg["auto_enabled"] = not self.cfg["auto_enabled"]
        save_config(self.cfg)
        winsound.Beep(900 if self.cfg["auto_enabled"] else 500, 100)

    def toggle_pause(self):
        self.paused = not self.paused
        winsound.Beep(500 if self.paused else 900, 100)

    def set_state(self, state):
        if state != self.state and self.icon:
            self.state = state
            self.icon.icon = self.icons[state]
            self.icon.title = f"TLI Autoloot - {STATE_LABELS[state]}"

    def start_rebind(self, which):
        self.rebinding = which
        self.icon.update_menu()
        winsound.Beep(1000, 80)
        winsound.Beep(1000, 80)

    def can_bind(self, vk):
        """Never the Windows keys (Win+Shift+S etc. while waiting). Sent keys
        also need a plain scancode: SendInput here can't send extended ones."""
        if vk in WIN_VKS:
            return False
        if self.rebinding == "pause" or vk == VK_ESCAPE:
            return True
        return vk not in EXTENDED_VKS and user32.MapVirtualKeyW(vk, 0) != 0

    def poll_rebind(self, prev):
        """Waits for the next key press and binds it (Esc cancels)."""
        now = {vk: key_down(vk) for vk in BINDABLE_VKS + [VK_ESCAPE]}
        pressed = [vk for vk in now if now[vk] and not prev.get(vk, True)
                   and self.can_bind(vk)]
        if VK_ESCAPE in pressed:
            self.rebinding = None
            self.icon.update_menu()
            winsound.Beep(400, 120)
        elif pressed:
            vk = pressed[0]
            if self.rebinding == "pause":
                self.cfg["pause_vk"] = vk
            else:
                # Sent keys go out as scancodes, so the layout (AZERTY) is respected.
                key = "key_scancode" if self.rebinding == "spam" else "auto_scancode"
                self.cfg[key] = user32.MapVirtualKeyW(vk, 0)
            save_config(self.cfg)
            self.rebinding = None
            winsound.Beep(1200, 120)
            winsound.Beep(1500, 120)
            self.icon.update_menu()
        return now

    def poll_hotkeys(self, prev):
        if self.rebinding:
            return self.poll_rebind(prev)
        pause_vk = self.cfg["pause_vk"]
        now = {vk: key_down(vk) for vk in (pause_vk, VK_F9, VK_F10, VK_F11, VK_F12)}
        ctrl = key_down(VK_CONTROL)
        pressed = lambda vk: now[vk] and not prev.get(vk, True)
        if pressed(pause_vk):
            self.toggle_pause()
        if ctrl and pressed(VK_F9):
            self.add_menu_point()
        if ctrl and pressed(VK_F10):
            self.clear_menu_points()
        if ctrl and pressed(VK_F11):
            self.recapture_hud()
        if ctrl and pressed(VK_F12):
            self.capture_auto_icon()
        return now

    def loop(self):
        prev_keys = {}
        next_tap = 0.0
        while self.running:
            prev_keys = self.poll_hotkeys(prev_keys)
            hwnd, name = foreground_process_name()
            if self.paused:
                state = "paused"
            elif name != self.cfg["process_name"]:
                state = "unfocused"
            elif self.menu_open(hwnd):
                state = "menu"
            else:
                state = "spamming"
            self.set_state(state)

            now = time.perf_counter()
            if state == "spamming" and now >= next_tap:
                tap_key(self.cfg["key_scancode"])
                next_tap = now + self.cfg["interval_seconds"]
            # Auto chest/mechanics runs whenever spam is on, menus included.
            if (state in ("spamming", "menu") and self.cfg["auto_enabled"]
                    and now >= self.next_auto):
                self.next_auto = now + 0.1
                self.auto_score = self.auto_icon_score(hwnd)
                seen = (self.auto_score is not None
                        and self.auto_score <= self.cfg["auto_match_ratio"])
                if seen != self.auto_seen:
                    self.auto_seen = seen
                    self.log(f"finger icon {'seen' if seen else 'gone'} "
                             f"(score {self.auto_score:.2f})")
                if seen:
                    # Held a bit longer than Q: interaction keys can miss a 15ms tap.
                    tap_key(self.cfg["auto_scancode"], hold=0.05)
                    self.auto_presses += 1
                    self.log(f"pressed {scancode_name(self.cfg['auto_scancode'])}")
                    self.next_auto = now + self.cfg["auto_interval_seconds"]
            elif state not in ("spamming", "menu"):
                self.auto_score, self.auto_seen = None, False
            time.sleep(0.01)

    def quit(self, icon, _item=None):
        self.running = False
        icon.stop()

    def run(self):
        menu = pystray.Menu(
            pystray.MenuItem("Options", lambda: self.options.show(), default=True),
            pystray.MenuItem(lambda _: f"Paused ({key_name(self.cfg['pause_vk'])})",
                             lambda: self.toggle_pause(), checked=lambda _: self.paused),
            pystray.MenuItem("Quit", self.quit),
        )
        self.icon = pystray.Icon("tli_autoloot", self.icons["unfocused"], "TLI Autoloot", menu)

        def setup(icon):
            icon.visible = True
            threading.Thread(target=self.loop, daemon=True).start()

        self.icon.run(setup=setup)


# --- Admin launch ------------------------------------------------------------
# The game runs elevated and ignores input from non-admin programs. A scheduled
# task set to "highest privileges" starts this exe as admin with no UAC prompt.
# The first launch asks for admin once to create that task.

INSTALL_FLAG = "--install-task"
NO_WINDOW = 0x08000000


def task_points_here():
    query = subprocess.run(["schtasks", "/query", "/tn", TASK_NAME, "/xml"],
                           capture_output=True, creationflags=NO_WINDOW)
    if query.returncode != 0:
        return False
    xml = query.stdout.decode("utf-16", errors="ignore")
    return sys.executable.lower() in xml.lower()


def install_task():
    ps = f"""
$action = New-ScheduledTaskAction -Execute '{sys.executable}'
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\\$env:USERNAME" -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $action -Principal $principal -Settings $settings -Force
"""
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, creationflags=NO_WINDOW)


def relaunch_as_admin():
    """Returns True when an elevated copy took over from this one."""
    if task_points_here():
        started = subprocess.run(["schtasks", "/run", "/tn", TASK_NAME],
                                 capture_output=True, creationflags=NO_WINDOW)
        return started.returncode == 0
    # First run (or the exe moved): one UAC prompt, the elevated copy installs the task.
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable,
                                                 INSTALL_FLAG, None, 1)
    return result > 32  # anything lower means the prompt was declined or failed


def main():
    frozen = getattr(sys, "frozen", False)
    is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    if frozen and not is_admin and relaunch_as_admin():
        sys.exit(0)
    if frozen and is_admin and INSTALL_FLAG in sys.argv:
        install_task()

    # One instance only: a second launch just exits. Taken after the relaunch
    # so the elevated copy isn't locked out by the copy that started it.
    kernel32.CreateMutexW(None, False, "Global\\TLI_QSpam_SingleInstance")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        sys.exit(0)
    Spammer().run()


if __name__ == "__main__":
    main()
