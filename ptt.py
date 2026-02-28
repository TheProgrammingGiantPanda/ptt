"""
Push-to-Talk for Claude Code (and any other window)
- Hold RIGHT CTRL to record
- Release to transcribe via local Whisper and type into active window
- Works across multiple Claude instances - types into whichever has focus
"""

import keyboard
import sounddevice as sd
import numpy as np
import requests
import threading
import time
import io
import wave
import traceback
import tkinter as tk
import ctypes
import ctypes.wintypes
import pystray
from PIL import Image, ImageDraw

WHISPER_URL = "http://localhost:2022/v1/audio/transcriptions"
PTT_KEY = "right ctrl"
SAMPLE_RATE = 16000

# Windows constants for preventing focus steal
GWL_EXSTYLE      = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080


def apply_no_activate(win):
    """Prevent a tkinter Toplevel from stealing focus when shown."""
    hwnd = win.winfo_id()
    style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ctypes.windll.user32.SetWindowLongW(
        hwnd, GWL_EXSTYLE,
        style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
    )

import os
_DIR = os.path.dirname(os.path.abspath(__file__))
LOG = open(os.path.join(_DIR, "ptt.log"), "w", buffering=1, encoding="utf-8")

def log(msg):
    print(msg, file=LOG, flush=True)


# ── Monitor detection ────────────────────────────────────────────────────────

def get_monitors():
    """Return list of (x, y, w, h) for each connected monitor."""
    monitors = []
    MonitorEnumProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        ctypes.c_ulong, ctypes.c_ulong,
        ctypes.POINTER(ctypes.wintypes.RECT),
        ctypes.c_ulong,
    )
    def callback(hMon, hdcMon, lpRect, dwData):
        r = lpRect.contents
        monitors.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True
    ctypes.windll.user32.EnumDisplayMonitors(None, None, MonitorEnumProc(callback), 0)
    return monitors if monitors else [(0, 0, 1920, 1080)]


# ── Tray icon ─────────────────────────────────────────────────────────────────

TRAY_COLORS = {
    "idle":       (108, 117, 125),   # grey
    "recording":  (230,  57,  70),   # red
    "processing": (244, 162,  97),   # orange
}

TRAY_TITLES = {
    "idle":       "Push-to-Talk (idle)",
    "recording":  "PTT \u2014 RECORDING",
    "processing": "PTT \u2014 PROCESSING",
}


def make_tray_image(state):
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = TRAY_COLORS[state]

    # Mic body (rounded rectangle)
    d.rounded_rectangle([18, 2, 46, 38], radius=14, fill=c)

    # Stand arc (half-circle below mic)
    d.arc([8, 22, 56, 52], start=0, end=180, fill=c, width=5)

    # Stem
    d.line([32, 52, 32, 60], fill=c, width=5)

    # Base
    d.line([20, 60, 44, 60], fill=c, width=5)

    return img


tray_icon: pystray.Icon = None


def update_tray(state):
    """Update tray icon color/tooltip for the given state."""
    if tray_icon is not None:
        tray_icon.icon = make_tray_image(state)
        tray_icon.title = TRAY_TITLES.get(state, "Push-to-Talk")


def setup_tray(root):
    global tray_icon

    def on_exit(icon, item):
        icon.stop()
        root.after(0, root.quit)

    menu = pystray.Menu(
        pystray.MenuItem("Push-to-Talk  [Right Ctrl]", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", on_exit),
    )

    tray_icon = pystray.Icon(
        "PTT",
        make_tray_image("idle"),
        TRAY_TITLES["idle"],
        menu=menu,
    )

    t = threading.Thread(target=tray_icon.run, daemon=True)
    t.start()
    log("Tray icon started.")


# ── Overlay ──────────────────────────────────────────────────────────────────

STATES = {
    "hidden":     (None,          None,       "#1a1a2e", "#e63946"),
    "recording":  ("\U0001f399",  "REC",      "#1a1a2e", "#e63946"),
    "processing": ("\u29d7",      "THINKING", "#1a1a2e", "#f4a261"),
}

WIN_W = 160
WIN_H = 110


class MonitorOverlay:
    """One overlay window per monitor."""
    def __init__(self, root, mon_x, mon_y, mon_w, mon_h):
        self.mon_x, self.mon_y, self.mon_w, self.mon_h = mon_x, mon_y, mon_w, mon_h

        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.88)
        win.geometry(f"{WIN_W}x{WIN_H}")
        win.withdraw()
        win.after(0, lambda: apply_no_activate(win))
        self.win = win

        # Icon label (large emoji/symbol)
        self.icon_label = tk.Label(
            win,
            font=("Segoe UI Emoji", 32),
            pady=4,
        )
        self.icon_label.pack(fill="x")

        # Status text
        self.text_label = tk.Label(
            win,
            font=("Segoe UI", 11, "bold"),
            pady=0,
            padx=8,
        )
        self.text_label.pack(fill="x")

        # Target window label
        self.target_label = tk.Label(
            win,
            font=("Segoe UI", 8),
            pady=2,
            padx=8,
        )
        self.target_label.pack(fill="x")

        # Accent bar at bottom
        self.bar = tk.Frame(win, height=4)
        self.bar.pack(fill="x", side="bottom")

    def _position(self):
        x = self.mon_x + (self.mon_w - WIN_W) // 2
        y = self.mon_y + (self.mon_h - WIN_H) // 2
        self.win.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")

    def set_state(self, state):
        if state == "hidden":
            self.win.withdraw()
            return

        icon, text, bg, accent = STATES[state]
        self.win.config(bg=bg)
        self.icon_label.config(text=icon, bg=bg, fg=accent)
        self.text_label.config(text=text, bg=bg, fg="#ffffff")
        self.target_label.config(text=target_window, bg=bg, fg="#aaaaaa")
        self.bar.config(bg=accent)
        self._position()
        self.win.deiconify()
        self.win.lift()
        self.win.update_idletasks()


overlays: list[MonitorOverlay] = []
target_window = ""
target_hwnd = None

BORDER = 4
HIGHLIGHT_COLOR = "#e63946"


_our_pid = ctypes.windll.kernel32.GetCurrentProcessId()


def get_focused_window_info():
    """Return (hwnd, title, rect) of the currently focused window.
    Returns (None, '', None) if the foreground window belongs to this process."""
    hwnd = ctypes.windll.user32.GetForegroundWindow()

    # Ignore our own tkinter/pystray windows
    win_pid = ctypes.c_ulong(0)
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(win_pid))
    if win_pid.value == _our_pid:
        return None, "", None

    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value
    title = title[:40] + "\u2026" if len(title) > 40 else title
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return hwnd, title, rect


class WindowHighlight:
    """Transparent overlay that draws a coloured border around a target window."""

    def __init__(self, root):
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-transparentcolor", "black")
        win.config(bg="black")
        win.withdraw()
        win.after(0, lambda: apply_no_activate(win))
        self.win = win

        # Outer border frame (coloured)
        self.border = tk.Frame(win, bg=HIGHLIGHT_COLOR)
        self.border.pack(fill="both", expand=True, padx=0, pady=0)

        # Inner area (transparent black)
        self.inner = tk.Frame(self.border, bg="black")
        self.inner.pack(fill="both", expand=True,
                        padx=BORDER, pady=BORDER)

    def show(self, rect):
        x, y = rect.left - BORDER, rect.top - BORDER
        w = rect.right - rect.left + BORDER * 2
        h = rect.bottom - rect.top + BORDER * 2
        self.border.config(bg=HIGHLIGHT_COLOR)
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        self.win.deiconify()
        self.win.lift()

    def hide(self):
        self.win.withdraw()


highlight: WindowHighlight = None


def set_overlay(state):
    """Thread-safe overlay update on all monitors."""
    for ov in overlays:
        ov.win.after(0, lambda o=ov: o.set_state(state))


def set_highlight(show):
    if highlight:
        if show and target_hwnd:
            rect = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(target_hwnd, ctypes.byref(rect))
            def _show_then_lift(r=rect):
                highlight.show(r)
                for ov in overlays:
                    ov.win.lift()
            highlight.win.after(0, _show_then_lift)
        else:
            highlight.win.after(0, highlight.hide)


# ── Audio / PTT logic ─────────────────────────────────────────────────────────

state_lock = threading.Lock()
state = "IDLE"
audio_chunks = []


def audio_callback(indata, frames, time_info, status):
    if state == "RECORDING":
        audio_chunks.append(indata.copy())


def save_wav(chunks, sample_rate):
    audio = np.concatenate(chunks, axis=0)
    audio_int16 = (audio * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_int16.tobytes())
    buf.seek(0)
    return buf


def transcribe(wav_buf):
    try:
        resp = requests.post(
            WHISPER_URL,
            files={"file": ("audio.wav", wav_buf, "audio/wav")},
            data={"model": "whisper-1"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("text", "").strip()
    except Exception as e:
        log(f"Transcription error: {e}")
        return None


def do_transcribe_and_type(chunks):
    global state
    try:
        set_overlay("processing")
        update_tray("processing")
        log("[...] Transcribing...")
        wav_buf = save_wav(chunks, SAMPLE_RATE)
        text = transcribe(wav_buf)
        if text:
            text = " ".join(text.split())
            log(f"[TXT] {text}")
            if target_hwnd:
                ctypes.windll.user32.SetForegroundWindow(target_hwnd)
            time.sleep(0.15)
            keyboard.write(text, delay=0.015)
        else:
            log("No transcription returned.")
    except Exception as e:
        log(f"Error in transcription thread: {e}")
    finally:
        with state_lock:
            state = "IDLE"
        set_overlay("hidden")
        set_highlight(False)
        update_tray("idle")
        log("[IDLE] Ready.")


def on_ptt_press(e):
    if e.name != PTT_KEY:
        return
    global state, audio_chunks, target_window, target_hwnd
    with state_lock:
        if state != "IDLE":
            return
        state = "RECORDING"
        audio_chunks = []
    target_hwnd, target_window, rect = get_focused_window_info()
    if target_hwnd is None:
        log("[WARN] PTT own window has focus — ignoring press.")
        with state_lock:
            state = "IDLE"
        return
    log(f"[REC] Recording... target: {target_window}")
    set_overlay("recording")
    set_highlight(True)
    update_tray("recording")


def on_ptt_release(e):
    if e.name != PTT_KEY:
        return
    global state
    with state_lock:
        if state != "RECORDING":
            return
        state = "PROCESSING"
        chunks = list(audio_chunks)

    if not chunks:
        log("No audio captured.")
        with state_lock:
            state = "IDLE"
        set_overlay("hidden")
        update_tray("idle")
        return

    log(f"[...] Captured {len(chunks)} chunks, processing...")
    t = threading.Thread(target=do_transcribe_and_type, args=(chunks,), daemon=True)
    t.start()


# ── Main ──────────────────────────────────────────────────────────────────────

def start_ptt(stream):
    keyboard.on_press_key(PTT_KEY, on_ptt_press, suppress=True)
    keyboard.on_release_key(PTT_KEY, on_ptt_release, suppress=True)
    log(f"PTT ready -- hold [{PTT_KEY.upper()}] to speak, release to transcribe")
    log(f"   Whisper: {WHISPER_URL}")
    log("Hooks registered.")
    stream.start()


def main():
    global overlay

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype='float32',
        callback=audio_callback,
        blocksize=1024,
    )

    root = tk.Tk()
    root.withdraw()  # hide the root window, we only use Toplevels

    global highlight
    highlight = WindowHighlight(root)

    monitors = get_monitors()
    log(f"Detected {len(monitors)} monitor(s): {monitors}")
    for (mx, my, mw, mh) in monitors:
        overlays.append(MonitorOverlay(root, mx, my, mw, mh))

    setup_tray(root)

    # Start PTT in background after tkinter is ready
    root.after(200, lambda: start_ptt(stream))

    try:
        root.mainloop()
    finally:
        stream.stop()
        stream.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FATAL ERROR: {e}")
        log(traceback.format_exc())
