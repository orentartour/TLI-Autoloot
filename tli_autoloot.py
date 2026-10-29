"""TLI Autoloot: a tray helper for Torchlight Infinite.

While the game is the active window:
- Spams Q every 0.1s, and stops while a menu is open (the flame icon in the
  top-right HUD disappears).
- Auto loot: presses D while the grey finger icon above the D slot shows,
  except while the DPS meter panel is open.
- IRIS 2 Full Auto, while the right button is held: right clicks while the
  right-click skill has charges, and at 0 charges presses Blink (A) until it
  goes on cooldown. IRIS 2 auto form: when the green S buff is missing from
  the top-left buff bar, presses Form (F). Both are off until turned on.

F8 pauses / resumes everything. Keys and switches are in the Options panel
(left-click the tray icon).

Tray icon colour: green = spamming, orange = menu open,
grey = game not focused, red = paused.
"""

import ctypes
import ctypes.wintypes as wt
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk

import pystray
from PIL import Image, ImageChops, ImageDraw, ImageGrab, ImageStat, ImageTk

HERE = os.path.dirname(os.path.abspath(__file__))
# Built as an exe: bundled files are unpacked to a temp dir; settings live in
# %APPDATA%.
BUNDLE_DIR = getattr(sys, "_MEIPASS", HERE)
TEMPLATE_DIR = os.path.join(BUNDLE_DIR, "templates")
if getattr(sys, "frozen", False):
    DATA_DIR = os.path.join(os.environ["APPDATA"], "TLI Autoloot")
    OLD_DATA_DIR = os.path.join(os.environ["APPDATA"], "TLIQSpam")  # before 2.1
else:
    DATA_DIR = OLD_DATA_DIR = HERE
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
APP_NAME = "TLI Autoloot"
TASK_NAME = "TLI Autoloot"

GAME_PROCESS = "torchlight_infinite.exe"
DEFAULT_CONFIG = {
    "interval_seconds": 0.1,
    "key_scancode": 0x1E,  # physical key that types Q on AZERTY
    "pause_vk": 0x77,  # F8
    "auto_enabled": True,
    "pirate_enabled": False,  # D on the purple pirate icon, same spot
    "pirate_v_scancode": 0x2F,  # V, pressed on the pirate V icon (top right)
    "auto_scancode": 0x20,  # D
    "auto_interval_seconds": 0.3,
    "zero_scancode": 0x10,  # Blink: physical key that types A on AZERTY
    # IRIS 2: all off until turned on in Options, then kept.
    "iris_open": False,  # Options section expanded
    "form_enabled": False,  # auto form
    "full_auto": False,  # Blink and right clicks in turn while right click is held
    "form_scancode": 0x21,  # F
    "form_interval_seconds": 1.5,
}

# Positions are in pixels of the game at 2560x1440; the UI scales with the
# window height.
REF_HEIGHT = 1440
# Flame icon in the top-right HUD. Anchored to the right edge, so x0/x1 are
# measured back from it.
FLAME_BOX = (84, 20, 32, 74)
# Share of the icon's bright outline that must line up to count as visible.
FLAME_MATCH = 0.45
# The icon's outline is light yellow; its orange glow stays below this.
BRIGHTNESS = 170
# How far (px) the icon may shift from its spot and still match.
SEARCH_MARGIN = 6
# Consecutive missed icon checks before calling it a menu (ignores flashes).
MISSES_FOR_MENU = 3
HUD_CHECK_INTERVAL = 0.05
# Max grey difference to the finger icon, as a fraction of the icon's own
# contrast: ~0 is a perfect match, ~1 is a flat patch of its average colour.
AUTO_MATCH_RATIO = 0.5
# Finger icon above the D slot: always at the same spot, given in pixels of a
# 1440-high window from its bottom centre, and the half-size of the square
# compared there.
AUTO_CENTER = (4, -322)
AUTO_HALF = 20
# The DPS meter panel (top left, dark slate) also shows the finger icon but
# must not get D. Area checked, in pixels of a 1440-high window from the top
# left, and the share of it that must be slate to skip the press.
PANEL_REGION = (60, 170, 420, 560)
PANEL_MIN_SHARE = 0.4
# Auto Pirate: the V key chip next to the purple star icon in the top right.
# Icons there come and go, so a band is searched: x0/x1 back from the right
# edge and y0/y1 from the top, in pixels of a 1440-high window.
PIRATE_V_BAND = (700, 243, 150, 287)
# Wait after pressing V before looking again.
PIRATE_V_INTERVAL = 1.0
# IRIS 2 Full Auto: the charge digit on the yellow badge of the right-click
# (mouse slot) skill. Left edge and top of its 14x16 box, from the bottom
# centre of a 1440-high window.
CHARGE_X, CHARGE_Y = -246, -140
CHARGE_SIZE = (14, 16)
# Digits are compared by shape (normalised correlation), which holds up when
# the cooldown overlay darkens the icon. 0 needs this score, and this lead
# over 1 and 2.
CHARGE_MIN_SCORE = 0.6
CHARGE_MIN_LEAD = 0.3
# IRIS 2 auto form: the green S form buff in the top-left buff bar. Left edges of the
# buff slots and their top, from the top left of a 1440-high window: buffs
# shift left as others expire. Only the icon's top is compared (the stack count
# sits in its bottom right), by shape like the charge digits.
FORM_SLOTS_X = [409 + round(54.375 * k) for k in range(12)]
FORM_Y = 26
FORM_SIZE = (42, 20)
FORM_MIN_SCORE = 0.6
# Consecutive missed buff checks before pressing Form.
FORM_MISSES = 2
# IRIS 2 Full Auto: the grey stop sign drawn over the Blink skill while it's on
# cooldown. Skill icon centres (mouse, A, Z, E, R, F) and their height, from
# the bottom centre of a 1440-high window, and the slot of each Blink key.
SKILL_SLOTS_X = [-240 + 94 * k for k in range(6)]
SKILL_Y = -157
SLOT_OF_SCANCODE = {0x10: 1, 0x11: 2, 0x12: 3, 0x13: 4, 0x21: 5}  # A Z E R F
# The sign is a ring (this radius) with a slash. Points on it are sampled
# against points either side: the sign is grey, so clearly less saturated than
# the icon around it, even under the glow of a press. Share of points that
# must say so, and by how much.
STOP_RADIUS = 18
STOP_MIN_SHARE = 0.45
STOP_SAT_GAP = 25
# Cooldown-free reads in a row before Blink counts as ready (the brightest
# frames of the press glow wash the sign out).
READY_READS = 2
# How often Full Auto looks at the screen, the gaps between Blink presses and
# between right clicks, and the pause between Blink going on cooldown and the
# first right click.
FULL_AUTO_TICK = 0.05
FULL_AUTO_BLINK_GAP = 0.1
FULL_AUTO_CLICK_GAP = 0.7
FULL_AUTO_DELAY = 0.3


def stop_sign_samples():
    """(on, inside, outside) offsets from the icon centre, at 1440p."""
    out = []
    for i in range(36):
        a = i * math.pi / 18
        c, s = math.cos(a), math.sin(a)
        out.append([(r * c, r * s) for r in (STOP_RADIUS, STOP_RADIUS - 5, STOP_RADIUS + 5)])
    for i in range(9):  # the slash, bottom left to top right
        t = (i - 4) / 4 * STOP_RADIUS * 0.6 * 0.707
        out.append([(t, -t), (t - 3.5, -t - 3.5), (t + 3.5, -t + 3.5)])
    return [[(round(x), round(y)) for x, y in pts] for pts in out]


STOP_SAMPLES = stop_sign_samples()

VK_ESCAPE = 0x1B
# Keys a pause bind can use: everything except mouse buttons.
BINDABLE_VKS = [vk for vk in range(0x08, 0xFF) if vk != VK_ESCAPE]
# Keys whose name lookup needs the "extended" flag (arrows, Insert, ...).
EXTENDED_VKS = set(range(0x21, 0x2F)) | {0x5B, 0x5C, 0x5D, 0x6F, 0x90}
WIN_VKS = {0x5B, 0x5C}

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Physical pixel coordinates, so positions work on scaled displays.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    user32.SetProcessDPIAware()

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


INPUT_MOUSE = 0
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010


def right_click(hold=0.03):
    for flags in (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP):
        inp = INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, 0, flags, 0, 0))
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        if flags == MOUSEEVENTF_RIGHTDOWN:
            time.sleep(hold)


# --- Physical right button ---------------------------------------------------
# Full Auto sends right clicks while the user holds the right button, so the
# button's async state can't tell whether it's still held. A low-level mouse
# hook sees only the user's own clicks (injected ones are flagged).

class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(wt.LPARAM, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.CallNextHookEx.restype = wt.LPARAM
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
kernel32.GetModuleHandleW.restype = wt.HMODULE
WH_MOUSE_LL, WM_RBUTTONDOWN, WM_RBUTTONUP, LLMHF_INJECTED = 14, 0x0204, 0x0205, 0x01


class RightButton:
    """Whether the user is physically holding the right mouse button."""

    def __init__(self):
        self.held = False
        self.proc = HOOKPROC(self.hook)  # kept referenced while the hook lives
        threading.Thread(target=self.run, daemon=True).start()

    def hook(self, code, wparam, lparam):
        if code == 0 and wparam in (WM_RBUTTONDOWN, WM_RBUTTONUP):
            info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if not info.flags & LLMHF_INJECTED:
                self.held = wparam == WM_RBUTTONDOWN
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def run(self):
        user32.SetWindowsHookExW(WH_MOUSE_LL, self.proc, kernel32.GetModuleHandleW(None), 0)
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


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


def ui_scale(hwnd):
    return client_size(hwnd)[1] / REF_HEIGHT


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


def load_flame_template():
    return bright_mask(Image.open(os.path.join(TEMPLATE_DIR, "flame.png")))


def half_size(img):
    """Compared at half resolution: softer on anti-aliasing."""
    return img.resize((img.width // 2, img.height // 2), Image.BOX)


def load_auto_template(name="auto.png"):
    """An icon above D (finger or pirate) in grey (half size), with its mean
    deviation from its average."""
    img = half_size(Image.open(os.path.join(TEMPLATE_DIR, name)).convert("L"))
    flat = Image.new("L", img.size, round(ImageStat.Stat(img).mean[0]))
    return img, max(ImageStat.Stat(ImageChops.difference(img, flat)).mean[0], 1.0)


def normalised(img):
    """Grey pixels scaled to mean 0 and spread 1: only the shape is left."""
    px = list(img.convert("L").getdata())
    mean = sum(px) / len(px)
    spread = (sum((p - mean) ** 2 for p in px) / len(px)) ** 0.5 or 1.0
    return [(p - mean) / spread for p in px]


def load_charge_templates():
    return {d: normalised(half_size(Image.open(os.path.join(TEMPLATE_DIR, f"charge_{d}.png"))))
            for d in "012"}


def load_form_template():
    return normalised(half_size(Image.open(os.path.join(TEMPLATE_DIR, "form.png"))))


# --- Config ------------------------------------------------------------------

def load_config():
    # Settings from before 2.1 lived in %APPDATA%\TLIQSpam: keep the keys.
    if not os.path.exists(CONFIG_PATH) and os.path.isdir(OLD_DATA_DIR) \
            and OLD_DATA_DIR != DATA_DIR:
        os.makedirs(DATA_DIR, exist_ok=True)
        old = os.path.join(OLD_DATA_DIR, "config.json")
        if os.path.exists(old):
            shutil.copy(old, CONFIG_PATH)
        shutil.rmtree(OLD_DATA_DIR, ignore_errors=True)
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        # Only known settings: drops ones older versions left behind.
        cfg.update({k: v for k, v in saved.items() if k in DEFAULT_CONFIG})
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
# Styled after Torchlight Infinite's own panels: dark slate, hairline borders,
# warm gold headings, bordered key chips and a round close button.

BG = "#161c22"       # panel
ROW = "#1d252d"      # row surface
LINE = "#33404b"     # hairlines and chip borders
FG = "#e9e4d8"       # warm off-white text
DIM = "#8a949c"
GOLD = "#d9a441"
FONT = ("Segoe UI", 9)
FONT_KEY = ("Segoe UI Semibold", 9)
FONT_HEAD = ("Segoe UI Semibold", 8)
WIDTH = 280
BINDS = [("pause", "Pause / resume"), ("spam", "Spam"), ("auto", "Loot")]
SEND_KEYS = {"spam": "key_scancode", "auto": "auto_scancode", "zero": "zero_scancode",
             "form": "form_scancode"}


def hex_color(rgb):
    return "#%02x%02x%02x" % rgb


class OptionsWindow:
    """Small borderless always-on-top panel: keys, the auto loot switch and
    the collapsible IRIS 2 section. Tk lives in its own thread and re-reads
    the app state every 100ms."""

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
        root.overrideredirect(True)  # no Windows title bar; the header drags
        root.attributes("-topmost", True)
        root.configure(bg=LINE)  # 1px border around the panel
        panel = tk.Frame(root, bg=BG, padx=12, pady=10)
        panel.pack(fill="both", expand=True, padx=1, pady=1)
        panel.columnconfigure(0, weight=1)

        # Header: crystal, title, status, close.
        head = tk.Frame(panel, bg=BG)
        head.grid(row=0, column=0, sticky="we")
        self.logo = ImageTk.PhotoImage(
            Image.open(os.path.join(BUNDLE_DIR, "fe.png")).resize((22, 22)), master=root)
        tk.Label(head, image=self.logo, bg=BG).pack(side="left")
        titles = tk.Frame(head, bg=BG)
        titles.pack(side="left", padx=(8, 0))
        tk.Label(titles, text=APP_NAME.upper(), font=("Segoe UI Semibold", 10),
                 bg=BG, fg=GOLD).pack(anchor="w")
        status = tk.Label(titles, font=("Segoe UI", 8), bg=BG, fg=DIM)
        status.pack(anchor="w")
        close = tk.Canvas(head, width=22, height=22, bg=BG, highlightthickness=0)
        close.pack(side="right")

        def draw_close(hover):
            close.delete("all")
            colour = FG if hover else DIM
            close.create_oval(2, 2, 20, 20, outline=colour, width=1)
            close.create_line(8, 8, 14, 14, fill=colour, width=2)
            close.create_line(14, 8, 8, 14, fill=colour, width=2)
        draw_close(False)
        close.bind("<Enter>", lambda _: draw_close(True))
        close.bind("<Leave>", lambda _: draw_close(False))
        close.bind("<Button-1>", lambda _: self.close())

        drag = {}
        def start_drag(e):
            drag["x"], drag["y"] = e.x_root - root.winfo_x(), e.y_root - root.winfo_y()
        def do_drag(e):
            root.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}")
        for w in (head, titles, *titles.winfo_children(), *head.winfo_children()[:1]):
            w.bind("<Button-1>", start_drag)
            w.bind("<B1-Motion>", do_drag)

        row_no = [1]
        def section(text):
            tk.Label(panel, text=text, font=FONT_HEAD, bg=BG, fg=GOLD, anchor="w").grid(
                row=row_no[0], column=0, sticky="we", pady=(10, 3))
            tk.Frame(panel, bg=LINE, height=1).grid(row=row_no[0], column=0, sticky="sew")
            row_no[0] += 1

        def row(label, parent=None):
            """A labelled row on the panel, or packed into a section's body."""
            f = tk.Frame(parent or panel, bg=ROW, padx=8, pady=4)
            if parent:
                f.pack(fill="x", pady=1)
            else:
                f.grid(row=row_no[0], column=0, sticky="we", pady=1)
                row_no[0] += 1
            tk.Label(f, text=label, font=FONT, bg=ROW, fg=FG, anchor="w").pack(side="left")
            return f

        # Keys: bordered chips like the game's key labels; click, then press a key.
        chips = {}

        def add_chip(which, label, parent=None):
            chip = tk.Label(row(label, parent), font=FONT_KEY, width=7, bg=BG, fg=FG,
                            highlightthickness=1, highlightbackground=LINE, cursor="hand2")
            chip.pack(side="right")
            chip.bind("<Button-1>", lambda _: app.start_rebind(which))
            chips[which] = chip

        section("KEYS")
        for which, label in BINDS:
            add_chip(which, label)

        # Auto loot switch.
        section("AUTO LOOT")
        switches = {}  # config key -> canvas

        def add_switch(label, key, toggle, parent=None):
            sw = tk.Canvas(row(label, parent), width=34, height=18, bg=ROW,
                           highlightthickness=0, cursor="hand2")
            sw.pack(side="right")
            sw.bind("<Button-1>", lambda _: toggle())
            switches[key] = sw

        def draw_switch(sw, on):
            sw.delete("all")
            track = GOLD if on else LINE
            sw.create_oval(1, 1, 17, 17, fill=track, outline=track)
            sw.create_oval(17, 1, 33, 17, fill=track, outline=track)
            sw.create_rectangle(9, 1, 25, 17, fill=track, outline=track)
            x = 18 if on else 2
            sw.create_oval(x, 2, x + 14, 16, fill=FG if on else DIM, outline="")

        add_switch("Chests & mechanics", "auto_enabled", app.toggle_auto)
        add_switch("Auto Pirate", "pirate_enabled", app.toggle_pirate)

        # IRIS 2, collapsible: Full Auto and auto form, each with its key.
        head2 = tk.Frame(panel, bg=BG, cursor="hand2")
        head2.grid(row=row_no[0], column=0, sticky="we", pady=(10, 3))
        tk.Frame(panel, bg=LINE, height=1).grid(row=row_no[0], column=0, sticky="sew")
        row_no[0] += 1
        tk.Label(head2, text="IRIS 2", font=FONT_HEAD, bg=BG, fg=GOLD).pack(side="left")
        chevron = tk.Label(head2, font=FONT_HEAD, bg=BG, fg=GOLD)
        chevron.pack(side="right")
        iris_summary = tk.Label(head2, font=FONT_HEAD, bg=BG, fg=DIM)
        iris_summary.pack(side="right", padx=(0, 6))
        for w in (head2, *head2.winfo_children()):
            w.bind("<Button-1>", lambda _: app.toggle_iris_open())
        iris_body = tk.Frame(panel, bg=BG)
        iris_body.grid(row=row_no[0], column=0, sticky="we")
        row_no[0] += 1

        add_switch("Full Auto (hold right click)", "full_auto", app.toggle_full_auto, iris_body)
        add_chip("zero", "Blink key", iris_body)
        add_switch("Auto Form", "form_enabled", app.toggle_form, iris_body)
        add_chip("form", "Form key", iris_body)

        def place(height):
            """Resizes the window, keeping its bottom edge where it is."""
            bottom = root.winfo_y() + root.winfo_height()
            root.geometry(f"{WIDTH}x{height}+{root.winfo_x()}+{bottom - height}")

        names = {"pause": lambda: key_name(app.cfg["pause_vk"])}
        names.update({which: lambda k=key: scancode_name(app.cfg[k])
                      for which, key in SEND_KEYS.items()})
        drawn = {}

        def refresh():
            if self.root is not root:
                return
            state = app.state or "unfocused"
            if app.rebinding:
                status.config(text="Press a key  ·  Esc cancels", fg=GOLD)
            else:
                status.config(text=f"● {STATE_LABELS[state]}",
                              fg=hex_color(STATE_COLORS[state]))
            for which, chip in chips.items():
                waiting = app.rebinding == which
                chip.config(text="..." if waiting else names[which](),
                            highlightbackground=GOLD if waiting else LINE,
                            fg=GOLD if waiting else FG)
            # Canvases are redrawn only when their value changes.
            for key, sw in switches.items():
                if drawn.get(key) != app.cfg[key]:
                    drawn[key] = app.cfg[key]
                    draw_switch(sw, drawn[key])
            on = app.cfg["full_auto"] or app.cfg["form_enabled"]
            iris_summary.config(text="ON" if on else "OFF", fg=GOLD if on else DIM)
            if drawn.get("open") != app.cfg["iris_open"]:
                drawn["open"] = app.cfg["iris_open"]
                chevron.config(text="▾" if drawn["open"] else "▸")
                if drawn["open"]:
                    iris_body.grid()
                else:
                    iris_body.grid_remove()
                if "placed" in drawn:
                    root.update_idletasks()
                    place(root.winfo_reqheight())
            root.after(100, refresh)

        # Bottom-right of the work area, above the tray.
        if not app.cfg["iris_open"]:
            iris_body.grid_remove()
        root.update_idletasks()
        work = wt.RECT()
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work), 0)  # SPI_GETWORKAREA
        root.geometry(f"{WIDTH}x{root.winfo_reqheight()}"
                      f"+{work.right - WIDTH - 16}+{work.bottom - root.winfo_reqheight() - 16}")
        drawn["placed"] = True
        try:  # Windows 11 rounded corners
            hwnd = user32.GetParent(root.winfo_id())
            pref = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
        except Exception:
            pass

        with self.lock:
            self.root = root
        refresh()
        root.mainloop()


# --- Main logic ----------------------------------------------------------------

class Autoloot:
    def __init__(self):
        self.cfg = load_config()
        self.flame = load_flame_template()
        self.paused = False
        self.rebinding = None  # a BINDS / SEND_KEYS name while waiting for a key
        self.auto_template = load_auto_template()
        self.pirate_template = load_auto_template("pirate.png")
        self.pirate_v_template = load_auto_template("pirate_v.png")
        self.next_pirate_v = 0.0
        self.pirate_v_seen = False  # V icon on screen at the last check
        self.next_auto = 0.0
        self.charge_templates = load_charge_templates()
        self.form_template = load_form_template()
        self.next_form = 0.0
        self.form_misses = 0
        self.right_button = RightButton()
        self.fa_phase = None  # Full Auto: None, "blink", "wait" or "click"
        self.fa_until = self.fa_next_press = self.fa_next_click = self.next_full_auto = 0.0
        self.ready_reads = READY_READS
        self.options = OptionsWindow(self)
        self.running = True
        self.state = None
        self.icon = None
        self.icons = {s: make_icon(c) for s, c in STATE_COLORS.items()}
        self.hud_misses = MISSES_FOR_MENU
        self.next_hud_check = 0.0

    def flame_visible(self, hwnd):
        w, h = client_size(hwnd)
        s = ui_scale(hwnd)
        ox, oy = client_origin(hwnd)
        right = ox + w
        x0, y0, x1, y1 = FLAME_BOX
        m = SEARCH_MARGIN
        area = bright_mask(ImageGrab.grab(
            bbox=(round(right - x0 * s) - m, round(oy + y0 * s) - m,
                  round(right - x1 * s) + m, round(oy + y1 * s) + m), all_screens=True))
        if area[0] < self.flame[0] or len(area[1]) < len(self.flame[1]):
            return True  # window too small to compare: don't block spamming
        return best_overlap(self.flame, area) >= FLAME_MATCH

    def menu_open(self, hwnd):
        now = time.perf_counter()
        if now >= self.next_hud_check:
            self.next_hud_check = now + HUD_CHECK_INTERVAL
            self.hud_misses = 0 if self.flame_visible(hwnd) else self.hud_misses + 1
        return self.hud_misses >= MISSES_FOR_MENU

    def auto_box(self, hwnd):
        """Screen box around the finger icon for the current window size."""
        w, h = client_size(hwnd)
        s = ui_scale(hwnd)
        ox, oy = client_origin(hwnd)
        dx, dy = AUTO_CENTER
        cx, cy = round(ox + w / 2 + dx * s), round(oy + h + dy * s)
        return cx - AUTO_HALF, cy - AUTO_HALF, cx + AUTO_HALF, cy + AUTO_HALF

    def auto_icon_visible(self, hwnd, templates):
        """True when one of these icons (auto_template etc.) shows above D."""
        area = half_size(ImageGrab.grab(bbox=self.auto_box(hwnd), all_screens=True).convert("L"))
        for tpl, contrast in templates:
            if area.size != tpl.size:
                area = area.resize(tpl.size, Image.BOX)  # window isn't 1440 high
            diff = ImageStat.Stat(ImageChops.difference(area, tpl)).mean[0]
            if diff <= contrast * AUTO_MATCH_RATIO:
                return True
        return False

    def pirate_v_visible(self, hwnd):
        """True when the V key chip of the pirate icon shows in the top right."""
        w, h = client_size(hwnd)
        s = ui_scale(hwnd)
        ox, oy = client_origin(hwnd)
        x0, y0, x1, y1 = PIRATE_V_BAND
        # Brought to 1440p, then to half size.
        band = ImageGrab.grab(bbox=(round(ox + w - x0 * s), round(oy + y0 * s),
                                    round(ox + w - x1 * s), round(oy + y1 * s)),
                              all_screens=True).convert("L")
        band = half_size(band.resize((x0 - x1, y1 - y0), Image.BOX))
        tpl, contrast = self.pirate_v_template
        tw, th = tpl.size
        for x in range(band.width - tw + 1):
            for y in range(band.height - th + 1):
                diff = ImageStat.Stat(ImageChops.difference(
                    band.crop((x, y, x + tw, y + th)), tpl)).mean[0]
                if diff <= contrast * AUTO_MATCH_RATIO:
                    return True
        return False

    def charges_at_zero(self, hwnd):
        """True when the right-click skill's charge badge reads 0."""
        w, h = client_size(hwnd)
        s = ui_scale(hwnd)
        ox, oy = client_origin(hwnd)
        cx, by = ox + w / 2, oy + h
        bw, bh = CHARGE_SIZE
        m = 2  # px of slack around the digit box, at 1440p
        left, right = CHARGE_X - m, CHARGE_X + bw + m
        top, bottom = CHARGE_Y - m, CHARGE_Y + bh + m
        # Brought to 1440p, then to half size.
        area = ImageGrab.grab(bbox=(round(cx + left * s), round(by + top * s),
                                    round(cx + right * s), round(by + bottom * s)),
                              all_screens=True).convert("L")
        area = half_size(area.resize((right - left, bottom - top), Image.BOX))
        tw, th = bw // 2, bh // 2
        for dx in range(m + 1):
            for dy in range(m + 1):
                live = normalised(area.crop((dx, dy, dx + tw, dy + th)))
                score = {d: sum(a * b for a, b in zip(live, tpl)) / len(live)
                         for d, tpl in self.charge_templates.items()}
                if (score["0"] >= CHARGE_MIN_SCORE
                        and score["0"] - max(score["1"], score["2"]) >= CHARGE_MIN_LEAD):
                    return True
        return False

    def blink_stop_sign(self, hwnd):
        """True when the Blink skill shows the grey cooldown stop sign."""
        w, h = client_size(hwnd)
        s = ui_scale(hwnd)
        ox, oy = client_origin(hwnd)
        slot = SLOT_OF_SCANCODE.get(self.cfg["zero_scancode"], 1)
        cx, cy = ox + w / 2 + SKILL_SLOTS_X[slot] * s, oy + h + SKILL_Y * s
        half = STOP_RADIUS + 8
        icon = ImageGrab.grab(bbox=(round(cx - half * s), round(cy - half * s),
                                    round(cx + half * s), round(cy + half * s)),
                              all_screens=True).convert("RGB")
        sat = icon.resize((2 * half, 2 * half), Image.BOX).convert("HSV").getchannel("S").load()
        grey = sum(sat[half + x, half + y] + STOP_SAT_GAP
                   < min(sat[half + ix, half + iy], sat[half + px, half + py])
                   for (x, y), (ix, iy), (px, py) in STOP_SAMPLES)
        return grey >= STOP_MIN_SHARE * len(STOP_SAMPLES)

    def blink_cooling(self, hwnd):
        """Blink is on cooldown; it counts as ready after READY_READS clear reads."""
        if self.blink_stop_sign(hwnd):
            self.ready_reads = 0
        else:
            self.ready_reads += 1
        return self.ready_reads < READY_READS

    def full_auto_step(self, hwnd, now):
        """One step of Full Auto while the right button is held. Right click,
        one every FULL_AUTO_CLICK_GAP, while the right-click skill has charges.
        At 0 charges, Blink until Blink is on cooldown; then, once there are
        charges, wait FULL_AUTO_DELAY and right click again. Without charges
        Blink keeps being pressed."""
        if now < self.next_full_auto:
            return
        self.next_full_auto = now + FULL_AUTO_TICK
        no_charges = self.charges_at_zero(hwnd)
        if self.fa_phase == "blink" or no_charges:
            if self.fa_phase != "blink":
                self.fa_phase, self.ready_reads = "blink", READY_READS
            if self.blink_cooling(hwnd) and not no_charges:
                self.fa_phase, self.fa_until = "wait", now + FULL_AUTO_DELAY
            elif now >= self.fa_next_press:
                tap_key(self.cfg["zero_scancode"], hold=0.05)
                self.fa_next_press = now + FULL_AUTO_BLINK_GAP
        elif self.fa_phase == "wait" and now < self.fa_until:
            pass
        else:
            self.fa_phase = "click"
            if now >= self.fa_next_click:
                right_click()
                self.fa_next_click = now + FULL_AUTO_CLICK_GAP

    def form_buff_visible(self, hwnd):
        """True when the green S form buff is in any buff slot."""
        s = ui_scale(hwnd)
        ox, oy = client_origin(hwnd)
        bw, bh = FORM_SIZE
        m = 2  # px of slack around each icon, at 1440p
        left, right = FORM_SLOTS_X[0] - m, FORM_SLOTS_X[-1] + bw + m
        top, bottom = FORM_Y - m, FORM_Y + bh + m
        # One grab of the whole bar, brought to 1440p, then to half size.
        strip = ImageGrab.grab(bbox=(round(ox + left * s), round(oy + top * s),
                                     round(ox + right * s), round(oy + bottom * s)),
                               all_screens=True).convert("L")
        strip = half_size(strip.resize((right - left, bottom - top), Image.BOX))
        tw, th = bw // 2, bh // 2
        tpl = self.form_template
        for x in FORM_SLOTS_X:
            sx = (x - left) // 2
            for dx in range(m + 1):
                for dy in range(m + 1):
                    live = normalised(strip.crop((sx + dx - 1, dy, sx + dx - 1 + tw, dy + th)))
                    if sum(a * b for a, b in zip(live, tpl)) / len(live) >= FORM_MIN_SCORE:
                        return True
        return False

    def dps_panel_open(self, hwnd):
        s = ui_scale(hwnd)
        ox, oy = client_origin(hwnd)
        x0, y0, x1, y1 = (round(v * s) for v in PANEL_REGION)
        shot = ImageGrab.grab(bbox=(ox + x0, oy + y0, ox + x1, oy + y1),
                              all_screens=True).convert("RGB").reduce(4)
        # Dark, cool grey: blue clearly above red, neither black nor bright.
        slate = sum(1 for r, g, b in shot.getdata()
                    if 30 <= max(r, g, b) <= 100 and b - r >= 12 and b >= g - 4)
        return slate >= PANEL_MIN_SHARE * shot.width * shot.height

    def toggle_auto(self):
        self.cfg["auto_enabled"] = not self.cfg["auto_enabled"]
        save_config(self.cfg)

    def toggle_pirate(self):
        self.cfg["pirate_enabled"] = not self.cfg["pirate_enabled"]
        save_config(self.cfg)

    def toggle_full_auto(self):
        self.cfg["full_auto"] = not self.cfg["full_auto"]
        save_config(self.cfg)

    def toggle_form(self):
        self.cfg["form_enabled"] = not self.cfg["form_enabled"]
        save_config(self.cfg)

    def toggle_iris_open(self):
        self.cfg["iris_open"] = not self.cfg["iris_open"]
        save_config(self.cfg)

    def toggle_pause(self):
        self.paused = not self.paused

    def set_state(self, state):
        if state != self.state and self.icon:
            self.state = state
            self.icon.icon = self.icons[state]
            self.icon.title = f"{APP_NAME} - {STATE_LABELS[state]}"

    def start_rebind(self, which):
        self.rebinding = which
        self.icon.update_menu()

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
        elif pressed:
            vk = pressed[0]
            if self.rebinding == "pause":
                self.cfg["pause_vk"] = vk
            else:
                # Sent keys go out as scancodes, so the layout (AZERTY) is respected.
                self.cfg[SEND_KEYS[self.rebinding]] = user32.MapVirtualKeyW(vk, 0)
            save_config(self.cfg)
            self.rebinding = None
            self.icon.update_menu()
        return now

    def poll_hotkeys(self, prev):
        if self.rebinding:
            return self.poll_rebind(prev)
        pause_vk = self.cfg["pause_vk"]
        now = {pause_vk: key_down(pause_vk)}
        if now[pause_vk] and not prev.get(pause_vk, True):
            self.toggle_pause()
        return now

    def loop(self):
        prev_keys = {}
        next_tap = 0.0
        while self.running:
            prev_keys = self.poll_hotkeys(prev_keys)
            hwnd, name = foreground_process_name()
            if self.paused:
                state = "paused"
            elif name != GAME_PROCESS:
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
            # Auto Pirate: V when the V icon shows in the top right. Checked
            # first: while it shows, the pirate icon gets no D (V goes first).
            if state != "spamming" or not self.cfg["pirate_enabled"]:
                self.pirate_v_seen = False
            elif now >= self.next_pirate_v:
                self.next_pirate_v = now + 0.3
                self.pirate_v_seen = self.pirate_v_visible(hwnd)
                if self.pirate_v_seen:
                    tap_key(self.cfg["pirate_v_scancode"], hold=0.05)
                    self.next_pirate_v = now + PIRATE_V_INTERVAL
            # Auto chest/mechanics and Auto Pirate run whenever spam is on,
            # menus included. Both icons show at the same spot above D.
            icons = [self.auto_template] if self.cfg["auto_enabled"] else []
            if self.cfg["pirate_enabled"] and not self.pirate_v_seen:
                icons.append(self.pirate_template)
            if state in ("spamming", "menu") and icons and now >= self.next_auto:
                self.next_auto = now + 0.1
                if self.auto_icon_visible(hwnd, icons) and not self.dps_panel_open(hwnd):
                    # Held a bit longer than Q: interaction keys can miss a 15ms tap.
                    tap_key(self.cfg["auto_scancode"], hold=0.05)
                    self.next_auto = now + self.cfg["auto_interval_seconds"]
            # IRIS 2 Full Auto, while the right button is held.
            if (state == "spamming" and self.cfg["auto_enabled"]
                    and self.cfg["full_auto"] and self.right_button.held):
                self.full_auto_step(hwnd, now)
            else:
                self.fa_phase = None
            # IRIS 2 auto form: Form (F) once when the buff is gone,
            # then time for it to come back before checking again.
            if (state == "spamming" and self.cfg["form_enabled"]
                    and now >= self.next_form):
                self.next_form = now + 0.2
                self.form_misses = 0 if self.form_buff_visible(hwnd) else self.form_misses + 1
                if self.form_misses >= FORM_MISSES:
                    tap_key(self.cfg["form_scancode"], hold=0.05)
                    self.form_misses = 0
                    self.next_form = now + self.cfg["form_interval_seconds"]
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
        self.icon = pystray.Icon("tli_autoloot", self.icons["unfocused"], APP_NAME, menu)

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
    kernel32.CreateMutexW(None, False, "Global\\TLI_Autoloot_SingleInstance")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        sys.exit(0)
    Autoloot().run()


if __name__ == "__main__":
    main()
