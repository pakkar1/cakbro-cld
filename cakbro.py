#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CakBro - Safe Exam Browser v2.10.2  (port Python / PySide6)
Windows, Linux, macOS - satu kode, dikompilasi dengan PyInstaller lewat GitHub Actions.

Perbedaan utama dari versi AutoHotkey:
  * Browser kiosk ditanam di dalam aplikasi (QtWebEngine / Chromium), bukan
    Edge/Chrome/Brave/Firefox eksternal. Jadi tidak perlu taskkill browser,
    tidak perlu mengatur ukuran jendela browser, dan judul halaman
    (jembatan "|CAKBRO_AHK|") dibaca langsung dari QWebEnginePage.title().
  * Pemblokiran tombol: di dalam aplikasi (semua OS) + hook keyboard tingkat OS
    pada Windows (Win, Alt+Tab, Alt+F4, Ctrl+Esc, PrintScreen) + presentation
    options pada macOS (Dock/menu bar/Cmd+Tab). Linux: lihat README.
  * Mode pengembangan: jalankan dengan  python cakbro.py --dev

Keluar: CTRL+ALT+SHIFT+Q
"""
from __future__ import annotations

import atexit
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from PySide6.QtCore import QLockFile, Qt, QTimer, QUrl
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QStackedLayout, QVBoxLayout, QWidget,
)
import shiboken6

try:
    import psutil
except ImportError:  # aplikasi tetap jalan, hanya tanpa penutup aplikasi terlarang
    psutil = None

# ============================================================
# KONFIGURASI (hardcoded, sama seperti versi AHK)
# ============================================================
EXAM_URL = "https://ujikom.pakkar.my.id/2026/09/uji-kompetensi.html"
APP_VERSION = "2.10.2"
APP_TITLE = "CakBro"
UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/pakkar1/cakbro-cld/main/latest.ini"
UPDATE_BASE_URL = "https://github.com/pakkar1/cakbro-cld/releases/latest/download/"
# Nama file rilis per OS. macOS tidak punya auto-install (hanya pemberitahuan).
UPDATE_ASSETS = {"win32": "CakBro.exe", "linux": "CakBro-linux"}

BAR_COLOR = "1a1a2e"
BAR_TEXT_COLOR = "00d4ff"
REFRESH_BTN_COLOR = "0f3460"
APP_LANGUAGE = "id"
BAR_HEIGHT = 42
EXPIRED_AUTO_HOME_MS = 5000
BRIDGE_MARKER = "|CAKBRO_AHK|"
BRIDGE_RE = re.compile(r"^(ON|OFF)\|([0-9]+)\|([0-9]+)\|([A-Za-z0-9_-]*)\|")

DEV = "--dev" in sys.argv
FROZEN = bool(getattr(sys, "frozen", False))
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
PLATFORM_KEY = "win32" if IS_WIN else "darwin" if IS_MAC else "linux"


def data_dir() -> Path:
    if IS_WIN:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif IS_MAC:
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "CakBro"


DATA_DIR = data_dir()
PROFILE_DIR = DATA_DIR / "CakBroProfile"   # profil kiosk khusus (dihapus saat Home)

# ============================================================
# DAFTAR BLOKIR
# ============================================================
BLOCKED_KEYS = {
    # Pintasan browser
    "Ctrl+T", "Ctrl+N", "Ctrl+Shift+N", "Ctrl+W", "Ctrl+Shift+W", "Ctrl+L", "Ctrl+D",
    "Ctrl+H", "Ctrl+J", "Ctrl+Shift+I", "Ctrl+Shift+J", "Ctrl+Shift+C", "Ctrl+U",
    "Ctrl+P", "Ctrl+O", "Ctrl+S", "Ctrl+G", "Ctrl+Shift+Del", "Ctrl+Tab",
    "Ctrl+Backtab", "Ctrl+Shift+Backtab", "Ctrl+Shift+Tab", "Ctrl+F5", "Ctrl+Shift+T",
    "Ctrl+K", "Ctrl+E", "Ctrl+Q", "Ctrl+M",
    # Alt / Ctrl sistem
    "Alt+Tab", "Alt+F4", "Alt+Esc", "Alt+Space", "Alt+F8", "Ctrl+Esc", "Ctrl+Shift+Esc",
    # Tombol fungsi
    "F1", "F3", "F6", "F7", "F10", "F11", "F12",
    # Screenshot & menu
    "Print", "Alt+Print", "Ctrl+Print", "Menu", "Ctrl+Alt+A", "Ctrl+Alt+S",
}

FORBIDDEN_PROCESSES = {
    "win32": {
        "taskmgr.exe", "cmd.exe", "powershell.exe", "windowsterminal.exe",
        "snippingtool.exe", "screensketch.exe", "screenclippinghost.exe",
        "regedit.exe", "control.exe", "mmc.exe", "osk.exe",
        "calc.exe", "notepad.exe", "mspaint.exe", "wmplayer.exe", "vlc.exe",
    },
    "linux": {
        "gnome-terminal", "gnome-terminal-server", "konsole", "xterm", "xfce4-terminal",
        "tilix", "alacritty", "kitty", "terminator", "lxterminal", "mate-terminal",
        "gnome-system-monitor", "ksysguard", "plasma-systemmonitor",
        "gnome-screenshot", "flameshot", "spectacle", "shutter", "ksnip",
        "gnome-calculator", "kcalc", "gedit", "kate", "vlc",
    },
    "darwin": {
        "terminal", "iterm2", "activity monitor", "screenshot", "screencaptureui",
        "calculator", "textedit", "quicktime player", "vlc",
    },
}


# ============================================================
# HELPER PLATFORM
# ============================================================
if IS_WIN:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _LRESULT = ctypes.c_ssize_t
    _HOOKPROC = ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

    _user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.FindWindowExW.restype = wintypes.HWND
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    _user32.SetWindowsHookExW.restype = wintypes.HHOOK
    _user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    _user32.CallNextHookEx.restype = _LRESULT
    _user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    class _KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                    ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    def _each_window(cls: str):
        hwnd = None
        while True:
            hwnd = _user32.FindWindowExW(None, hwnd, cls, None)
            if not hwnd:
                return
            yield hwnd

    def set_taskbar_visible(show: bool) -> None:
        for cls in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            for hwnd in _each_window(cls):
                _user32.ShowWindow(hwnd, 5 if show else 0)  # SW_SHOW / SW_HIDE

    def close_explorer_windows() -> None:
        for cls in ("CabinetWClass", "ExploreWClass"):
            for hwnd in _each_window(cls):
                _user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE

    class WinKeyHook:
        """Hook keyboard tingkat rendah: blokir Win, Alt+Tab, Alt+F4, Ctrl+Esc, PrintScreen."""

        def __init__(self) -> None:
            self._hook = None
            self._proc = _HOOKPROC(self._callback)  # simpan referensi agar tidak di-GC

        def _callback(self, n_code, w_param, l_param):
            if n_code == 0:  # HC_ACTION
                kb = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                vk = kb.vkCode
                alt = bool(kb.flags & 0x20)
                ctrl = bool(_user32.GetAsyncKeyState(0x11) & 0x8000)
                if vk in (0x5B, 0x5C, 0x5D, 0x2C):          # LWin, RWin, Apps, PrintScreen
                    return 1
                if alt and vk in (0x09, 0x1B, 0x73, 0x20, 0x77):  # Tab, Esc, F4, Space, F8
                    return 1
                if ctrl and vk == 0x1B:                      # Ctrl+Esc / Ctrl+Shift+Esc
                    return 1
            return _user32.CallNextHookEx(self._hook, n_code, w_param, l_param)

        def install(self) -> None:
            if self._hook:
                return
            self._hook = _user32.SetWindowsHookExW(13, self._proc, _kernel32.GetModuleHandleW(None), 0)

        def remove(self) -> None:
            if self._hook:
                _user32.UnhookWindowsHookEx(self._hook)
                self._hook = None
else:
    def set_taskbar_visible(show: bool) -> None:  # noqa: D401 - no-op di OS lain
        return

    def close_explorer_windows() -> None:
        return

    class WinKeyHook:  # placeholder
        def install(self) -> None: ...
        def remove(self) -> None: ...


def mac_lockdown(enable: bool) -> None:
    """Sembunyikan Dock/menu bar dan nonaktifkan Cmd+Tab, Force Quit, dsb. (macOS)."""
    if not IS_MAC:
        return
    try:
        import AppKit
        opts = 0
        if enable:
            opts = (AppKit.NSApplicationPresentationHideDock
                    | AppKit.NSApplicationPresentationHideMenuBar
                    | AppKit.NSApplicationPresentationDisableAppleMenu
                    | AppKit.NSApplicationPresentationDisableProcessSwitching
                    | AppKit.NSApplicationPresentationDisableForceQuit
                    | AppKit.NSApplicationPresentationDisableSessionTermination
                    | AppKit.NSApplicationPresentationDisableHideApplication)
        AppKit.NSApplication.sharedApplication().setPresentationOptions_(opts)
    except Exception as exc:  # pyobjc tidak ada / kombinasi opsi ditolak
        print("[CakBro] mac_lockdown gagal:", exc, file=sys.stderr)


def kill_forbidden_apps() -> None:
    if psutil is None:
        return
    names = FORBIDDEN_PROCESSES.get(PLATFORM_KEY, set())
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info["name"] or "").lower() in names:
                proc.kill()
        except psutil.Error:
            pass


# ============================================================
# AUTO-UPDATE (GitHub, verifikasi SHA-256) - hanya untuk build hasil kompilasi
# ============================================================
def _http_get(url: str, timeout: float = 5.0) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"CakBroUpdater/{APP_VERSION}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read() if resp.status == 200 else None
    except Exception:
        return None


def is_newer_version(remote: str, current: str) -> bool:
    try:
        r = [int(x) for x in remote.split(".")]
        c = [int(x) for x in current.split(".")]
    except ValueError:
        return False
    return len(r) == 3 and len(c) == 3 and r > c


def check_for_update() -> bool:
    """True jika updater sudah dijalankan dan aplikasi harus keluar."""
    if not FROZEN or DEV:
        return False
    raw = _http_get(UPDATE_MANIFEST_URL, 5)
    if raw is None:
        return False
    text = raw.decode("utf-8", "ignore").lstrip("\ufeff")
    m = re.search(r"^\s*version\s*=\s*(\d+\.\d+\.\d+)\s*$", text, re.I | re.M)
    if not m or not is_newer_version(m.group(1), APP_VERSION):
        return False
    remote_version = m.group(1)
    asset = UPDATE_ASSETS.get(PLATFORM_KEY)
    if not asset:
        return False  # macOS: tidak ada auto-install
    hash_key = "sha256" if IS_WIN else f"sha256_{PLATFORM_KEY}"
    hm = re.search(rf"^\s*{hash_key}\s*=\s*([A-Fa-f0-9]{{64}})\s*$", text, re.I | re.M)
    if not hm:
        return False
    expected = hm.group(1).lower()

    upd_dir = DATA_DIR / "Update"
    upd_dir.mkdir(parents=True, exist_ok=True)
    staged = upd_dir / f"CakBro-{remote_version}.download"
    try:
        staged.unlink(missing_ok=True)
        digest = hashlib.sha256()
        req = urllib.request.Request(UPDATE_BASE_URL + asset,
                                     headers={"User-Agent": f"CakBroUpdater/{APP_VERSION}"})
        with urllib.request.urlopen(req, timeout=30) as resp, open(staged, "wb") as fh:
            if resp.status != 200:
                return False
            while chunk := resp.read(1 << 16):
                digest.update(chunk)
                fh.write(chunk)
        if digest.hexdigest() != expected:
            staged.unlink(missing_ok=True)
            return False
    except Exception:
        staged.unlink(missing_ok=True)
        return False

    target, pid = sys.executable, os.getpid()
    try:
        if IS_WIN:
            script = upd_dir / "apply_update.bat"
            script.write_text(
                "@echo off\r\nset /a n=0\r\n:wait\r\n"
                f'tasklist /FI "PID eq {pid}" 2>NUL | find "{pid}" >NUL\r\n'
                "if not errorlevel 1 (timeout /t 1 >NUL & goto wait)\r\n:swap\r\n"
                f'move /Y "{staged}" "{target}" >NUL 2>&1\r\n'
                "if errorlevel 1 (set /a n+=1 & if %n% LSS 30 (timeout /t 1 >NUL & goto swap))\r\n"
                f'start "" "{target}"\r\ndel "%~f0"\r\n', encoding="utf-8")
            subprocess.Popen(["cmd", "/c", str(script)], creationflags=0x08000000 | 0x00000008,
                             close_fds=True)
        else:
            script = upd_dir / "apply_update.sh"
            script.write_text(
                "#!/bin/sh\n"
                f"while kill -0 {pid} 2>/dev/null; do sleep 1; done\n"
                f'chmod +x "{staged}" && mv -f "{staged}" "{target}"\n'
                f'nohup "{target}" >/dev/null 2>&1 &\nrm -- "$0"\n', encoding="utf-8")
            script.chmod(0o755)
            subprocess.Popen(["/bin/sh", str(script)], start_new_session=True, close_fds=True)
    except Exception:
        staged.unlink(missing_ok=True)
        return False
    return True


# ============================================================
# WEB PAGE
# ============================================================
class KioskPage(QWebEnginePage):
    def __init__(self, profile: QWebEngineProfile, kiosk: "Kiosk") -> None:
        super().__init__(profile)
        self._kiosk = kiosk
        self.titleChanged.connect(kiosk.apply_bridge)
        self.renderProcessTerminated.connect(lambda *_: QTimer.singleShot(500, self.reload))
        try:                                   # Qt >= 6.8
            self.permissionRequested.connect(lambda perm: perm.deny())
        except AttributeError:                 # Qt < 6.8
            self.featurePermissionRequested.connect(
                lambda url, feat: self.setFeaturePermission(
                    url, feat, QWebEnginePage.PermissionPolicy.PermissionDeniedByUser))

    def createWindow(self, _wtype):            # popup (mis. login Google)
        return self._kiosk.open_popup(self.profile())

    def chooseFiles(self, _mode, _old, _mimes):  # dialog Open/Save diblokir seperti versi AHK
        return []


# ============================================================
# OVERLAY WAKTU HABIS
# ============================================================
class ExpiredOverlay(QWidget):
    def __init__(self, parent: QWidget, on_back) -> None:
        super().__init__(parent)
        self.setObjectName("expiredOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("#expiredOverlay{background-color:rgba(16,24,39,218);}"
                           "QLabel{background:transparent;}")
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(14)

        def label(text, size, bold, color):
            lb = QLabel(text)
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lb.setWordWrap(True)
            f = QFont("Segoe UI", size)
            f.setBold(bold)
            lb.setFont(f)
            lb.setStyleSheet(f"color:#{color};")
            lay.addWidget(lb)
            return lb

        label("Waktu ujian telah berakhir", 24, True, "FFFFFF")
        label("Akses ke formulir ujian telah dihentikan.", 12, False, "E2E8F0")
        label("Jika tidak ditekan, browser ditutup dan profil/cookie kiosk dihapus. "
              "Jawaban yang belum dikirim bisa hilang.", 10, False, "E2E8F0")
        self.countdown = label("", 11, True, "FBBF24")
        btn = QPushButton("Kembali")
        btn.setFixedSize(170, 44)
        btn.clicked.connect(on_back)
        lay.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)


# ============================================================
# APLIKASI UTAMA
# ============================================================
class Kiosk(QWidget):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.setWindowTitle(APP_TITLE)

        # --- status (setara variabel global AHK) ---
        self.exiting = False
        self.exiting_dialog = False
        self.home_resetting = False
        self.exam_expired = False
        self.bridge_active = False
        self.bridge_remaining = 0
        self.bridge_token_wait = 0
        self.bridge_token = ""
        self.bridge_sync = 0.0
        self.expired_auto_home_at = 0.0

        self.profile: QWebEngineProfile | None = None
        self.page: KioskPage | None = None
        self.view: QWebEngineView | None = None
        self.popups: list[tuple[QWebEngineView, KioskPage]] = []
        self.key_hook = WinKeyHook()
        self.killer_enabled = FROZEN and not DEV

        self._build_ui()
        self._install_shortcuts()

        # --- timer ---
        self._timer(500, self.security_check)
        self._timer(1000, self.kill_apps_tick)
        self._timer(1000, self.update_clock)
        self._timer(250, self.poll_bridge)
        self._timer(300, self.keep_focus)
        self.expired_tick = self._timer(1000, self.update_expired_countdown, start=False)
        self.auto_home_timer = QTimer(self)
        self.auto_home_timer.setSingleShot(True)
        self.auto_home_timer.timeout.connect(self.auto_home_after_expiry)

    # ---------- pintasan keyboard ----------
    def _install_shortcuts(self) -> None:
        """Pintasan diblokir lewat QShortcut level aplikasi (stabil di semua OS).
        CATATAN: jangan memakai event filter Python pada QApplication - itu membuat
        QtWebEngine crash (segfault)."""
        self._shortcuts: list[QShortcut] = []
        seen: set[str] = set()

        def add(seq: str, slot) -> None:
            norm = QKeySequence(seq).toString()
            if not norm or norm in seen:
                return
            seen.add(norm)
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.setAutoRepeat(False)
            sc.activated.connect(slot)
            self._shortcuts.append(sc)

        # CTRL+ALT+SHIFT+Q  (macOS: tombol Control dipetakan Qt sebagai Meta)
        add("Ctrl+Alt+Shift+Q", self.trigger_exit)
        add("Meta+Alt+Shift+Q", self.trigger_exit)
        if not DEV:
            for seq in BLOCKED_KEYS:
                add(seq, lambda: None)

    # ---------- util ----------
    def _timer(self, ms, fn, start=True) -> QTimer:
        t = QTimer(self)
        t.timeout.connect(fn)
        if start:
            t.start(ms)
        return t

    def _msgbox(self, icon, title, text, buttons, default=None) -> int:
        box = QMessageBox(icon, title, text, buttons, self)
        box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        if default is not None:
            box.setDefaultButton(default)
        return box.exec()

    # ---------- UI ----------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setStyleSheet("background:#000;")

        container = QWidget()
        self.stack = QStackedLayout(container)
        self.stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.overlay = ExpiredOverlay(container, self.return_home_from_expiry)
        self.stack.addWidget(self.overlay)
        self.overlay.hide()          # hide SETELAH addWidget (layout bisa menampilkannya kembali)
        root.addWidget(container, 1)

        # ---- bottom bar ----
        bar = QFrame()
        bar.setObjectName("bar")
        bar.setFixedHeight(BAR_HEIGHT)
        bar.setStyleSheet(
            f"#bar{{background:#{BAR_COLOR};border-top:2px solid #{BAR_TEXT_COLOR};}}"
            "QLabel{background:transparent;}"
            f"QPushButton{{background:#{REFRESH_BTN_COLOR};color:#fff;border:1px solid #444;"
            "border-radius:4px;padding:0 10px;}"
            "QPushButton:hover{background:#16478a;}QPushButton:disabled{color:#666;}")
        h = QHBoxLayout(bar)
        h.setContentsMargins(14, 2, 14, 0)
        h.setSpacing(10)

        brand = QLabel(f"CakBro V{APP_VERSION}")
        brand.setFont(self._font("Segoe UI", 11, True))
        brand.setStyleSheet(f"color:#{BAR_TEXT_COLOR};")
        h.addWidget(brand)
        h.addWidget(self._sep())

        self.refresh_btn = QPushButton("\u27F3 Refresh")
        self.refresh_btn.setFixedSize(90, 29)
        self.refresh_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.refresh_btn.clicked.connect(self.refresh_browser)
        h.addWidget(self.refresh_btn)
        h.addWidget(self._sep())

        self.status = QLabel("Menunggu ujian...")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setFont(self._font("Segoe UI", 9, True))
        self.status.setStyleSheet("color:#E2E8F0;")
        h.addWidget(self.status, 1)

        self.home_btn = QPushButton("Home")
        self.home_btn.setFixedSize(80, 29)
        self.home_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.home_btn.clicked.connect(self.return_home_from_bar)
        self.home_btn.hide()
        h.addWidget(self.home_btn)
        h.addWidget(self._sep())

        pk = QLabel("Pakkar")
        pk.setFont(self._font("Segoe UI", 9, False))
        pk.setStyleSheet("color:#4ade80;")
        h.addWidget(pk)

        self.clock = QLabel(time.strftime("%H:%M:%S"))
        mono = QFont("Consolas", 12)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.clock.setFont(mono)
        self.clock.setStyleSheet("color:#AAAAAA;")
        self.clock.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.clock.setMinimumWidth(100)
        h.addWidget(self.clock)
        root.addWidget(bar)

    @staticmethod
    def _font(name, size, bold) -> QFont:
        f = QFont(name, size)
        f.setBold(bold)
        return f

    @staticmethod
    def _sep() -> QFrame:
        s = QFrame()
        s.setFixedSize(1, 28)
        s.setStyleSheet("background:#444444;")
        return s

    # ---------- start / stop ----------
    def start(self) -> None:
        if not DEV:
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
            set_taskbar_visible(False)
            self.key_hook.install()
            mac_lockdown(True)
        self.launch_browser(EXAM_URL)
        screen = self.app.primaryScreen().geometry()
        if DEV:
            self.resize(1280, 800)
            self.show()
        elif IS_MAC:
            self.setGeometry(screen)
            self.show()
        else:
            self.showFullScreen()
        self.raise_()
        self.activateWindow()

    def full_cleanup(self) -> None:
        for t in self.findChildren(QTimer):
            t.stop()
        self.key_hook.remove()
        set_taskbar_visible(True)
        mac_lockdown(False)

    def quit_app(self) -> None:
        self.exiting = True
        self.full_cleanup()
        self.destroy_browser()      # page/profile harus dibebaskan sebelum QApplication selesai
        self.app.quit()

    def closeEvent(self, ev) -> None:  # abaikan penutupan dari luar kecuali keluar resmi
        if self.exiting:
            ev.accept()
        else:
            ev.ignore()

    # ---------- browser ----------
    def launch_browser(self, url: str) -> None:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        prof = QWebEngineProfile("CakBro")
        prof.setPersistentStoragePath(str(PROFILE_DIR))
        prof.setCachePath(str(PROFILE_DIR / "cache"))
        prof.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)
        prof.setHttpAcceptLanguage(APP_LANGUAGE)
        # Hilangkan token "QtWebEngine/x" agar login Google tidak menolak browser tertanam.
        prof.setHttpUserAgent(re.sub(r"\s*QtWebEngine/[\d.]+", "", prof.httpUserAgent()))
        prof.downloadRequested.connect(lambda d: d.cancel())
        st = prof.settings()
        A = QWebEngineSettings.WebAttribute
        st.setAttribute(A.JavascriptCanOpenWindows, True)
        st.setAttribute(A.LocalStorageEnabled, True)
        st.setAttribute(A.PluginsEnabled, False)
        st.setAttribute(A.ScreenCaptureEnabled, False)
        st.setAttribute(A.AutoLoadIconsForPage, False)

        page = KioskPage(prof, self)
        view = QWebEngineView()
        view.setPage(page)
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.profile, self.page, self.view = prof, page, view
        self.stack.insertWidget(0, view)
        view.setEnabled(not self.overlay.isVisible())
        view.load(QUrl(url))

    def destroy_browser(self) -> None:
        self.close_popups()
        if self.view is not None:
            self.stack.removeWidget(self.view)
            self.view.hide()
            self.view.setParent(None)
        for obj in (self.view, self.page, self.profile):   # urutan: view -> page -> profile
            if obj is not None:
                try:
                    shiboken6.delete(obj)
                except Exception:
                    pass
        self.view = self.page = self.profile = None

    def open_popup(self, profile: QWebEngineProfile) -> KioskPage:
        view = QWebEngineView()
        page = KioskPage(profile, self)
        view.setPage(page)
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        view.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        geo = self.app.primaryScreen().geometry()
        w, h = min(1000, geo.width() - 80), min(760, geo.height() - 120)
        view.setGeometry(geo.x() + (geo.width() - w) // 2, geo.y() + (geo.height() - h) // 2, w, h)
        page.windowCloseRequested.connect(lambda v=view: self._close_popup(v))
        self.popups.append((view, page))
        view.show()
        return page

    def _close_popup(self, view: QWebEngineView) -> None:
        for entry in list(self.popups):
            if entry[0] is view:
                self.popups.remove(entry)
                view.hide()
                QTimer.singleShot(0, lambda e=entry: [shiboken6.delete(o) for o in e
                                                      if shiboken6.isValid(o)])

    def close_popups(self) -> None:
        for view, page in self.popups:
            view.hide()
            for o in (view, page):
                if shiboken6.isValid(o):
                    shiboken6.delete(o)
        self.popups.clear()

    def refresh_browser(self) -> None:
        if self.exam_expired or self.view is None:
            return
        self.view.reload()

    # ---------- jembatan judul halaman ----------
    def poll_bridge(self) -> None:
        if self.home_resetting:
            return
        pages = ([self.page] if self.page else []) + [p for _, p in self.popups]
        for pg in pages:
            if shiboken6.isValid(pg) and self.apply_bridge(pg.title()):
                break

    def apply_bridge(self, title: str) -> bool:
        if self.home_resetting:
            return False
        pos = title.find(BRIDGE_MARKER)
        if pos < 0:
            return False
        m = BRIDGE_RE.match(title[pos + len(BRIDGE_MARKER):])
        if not m:
            return False
        state, remaining, token_wait, token = m.groups()
        if state == "OFF":
            self._reset_bridge()
        else:
            self.bridge_active = True
            self.bridge_remaining = int(remaining)
            self.bridge_token_wait = int(token_wait)
            self.bridge_token = "" if token == "-" else token
            self.bridge_sync = time.monotonic()
            if int(remaining) <= 0 and not self.exam_expired:
                self.exam_expired = True
                self.show_expired_overlay()
                self.update_exam_bar()
        return True

    def _reset_bridge(self) -> None:
        self.bridge_active = False
        self.bridge_remaining = 0
        self.bridge_token_wait = 0
        self.bridge_token = ""
        self.bridge_sync = 0.0

    @staticmethod
    def _mmss(sec: int) -> str:
        return f"{sec // 60}:{sec % 60:02d}"

    def update_exam_bar(self) -> None:
        show_home = False
        if self.exam_expired:
            text = "WAKTU HABIS"
        elif not self.bridge_active:
            text = "Menunggu ujian..."
        else:
            elapsed = int(time.monotonic() - self.bridge_sync)
            remaining = max(0, self.bridge_remaining - elapsed)
            token_wait = max(0, self.bridge_token_wait - elapsed)
            if remaining == 0:
                text = "WAKTU HABIS"
                if not self.exam_expired:
                    self.exam_expired = True
                    self.show_expired_overlay()
            elif token_wait > 0:
                text = f"Sisa {self._mmss(remaining)} | Token {self._mmss(token_wait)} lagi"
            elif self.bridge_token:
                text = f"Sisa {self._mmss(remaining)} | Token: {self.bridge_token}"
                show_home = True
            else:
                text = f"Sisa {self._mmss(remaining)} | Token belum diatur"
        self.status.setText(text)
        if self.exam_expired:
            self.home_btn.hide()
            self.refresh_btn.setEnabled(False)
        else:
            self.refresh_btn.setEnabled(True)
            self.home_btn.setVisible(show_home)

    def update_clock(self) -> None:
        self.clock.setText(time.strftime("%H:%M:%S"))
        self.update_exam_bar()

    # ---------- overlay waktu habis ----------
    def show_expired_overlay(self) -> None:
        if self.overlay.isVisible():
            return
        for view, _ in self.popups:
            view.hide()
        if self.view is not None:
            self.view.setEnabled(False)
        self.expired_auto_home_at = time.monotonic() + EXPIRED_AUTO_HOME_MS / 1000
        self.overlay.show()
        self.overlay.raise_()
        self.refresh_btn.setEnabled(False)
        self.home_btn.hide()
        self.update_expired_countdown()
        self.expired_tick.start()
        self.auto_home_timer.start(EXPIRED_AUTO_HOME_MS)

    def update_expired_countdown(self) -> None:
        if not self.overlay.isVisible() or not self.exam_expired or self.home_resetting:
            return
        left = max(0, int(-(-(self.expired_auto_home_at - time.monotonic()) // 1)))
        self.overlay.countdown.setText(f"Otomatis kembali ke jadwal dalam {left} detik.")

    def auto_home_after_expiry(self) -> None:
        if not self.exam_expired or not self.overlay.isVisible() or self.home_resetting:
            return
        self.expired_tick.stop()
        self.home_resetting = True
        self.execute_home_reset()

    def return_home_from_expiry(self) -> None:
        self.auto_home_timer.stop()
        self.expired_tick.stop()
        self.return_home_from_bar()

    # ---------- Home ----------
    def return_home_from_bar(self) -> None:
        if self.home_resetting or (not self.exam_expired and (not self.bridge_active or not self.bridge_token)):
            return
        self.auto_home_timer.stop()
        self.expired_tick.stop()
        self.home_resetting = True
        ans = self._msgbox(
            QMessageBox.Icon.Warning, "CakBro - Kembali ke Home",
            "Jika dilanjutkan semua data akan di hapus dan Jawaban yang belum dikirim akan hilang.\n\n"
            "Aplikasi kembali ke tampilan awal. Lanjutkan?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ans == QMessageBox.StandardButton.Yes:
            self.execute_home_reset()
        else:
            self.home_resetting = False
            if self.exam_expired and self.overlay.isVisible():
                self.overlay.countdown.setText("Waktu habis. Tekan Kembali untuk kembali ke jadwal.")

    def _pump(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.02)

    def execute_home_reset(self) -> None:
        self.auto_home_timer.stop()
        self.expired_tick.stop()
        base_url = EXAM_URL
        self._reset_bridge()
        self.exam_expired = False
        self.update_exam_bar()

        # Tutup semua tab/jendela kiosk, lalu hapus profil khusus (cookie, login, storage).
        self.destroy_browser()
        self._pump(0.5)
        for _ in range(10):
            shutil.rmtree(PROFILE_DIR, ignore_errors=True)
            if not PROFILE_DIR.exists():
                break
            self._pump(0.5)
        if PROFILE_DIR.exists():
            self._msgbox(
                QMessageBox.Icon.Critical, "CakBro - Gagal Menghapus Profil",
                "Profil kiosk tidak berhasil dihapus. Browser tidak dibuka ulang agar sesi login "
                "lama tidak tertinggal.\n\nPeriksa izin folder:\n" + str(PROFILE_DIR),
                QMessageBox.StandardButton.Ok)
            self.quit_app()
            return

        self.overlay.hide()
        # Parameter sekali pakai: HTML menghapus sessionStorage lama lalu membersihkan URL.
        sep = "&" if "?" in base_url else "?"
        self.launch_browser(f"{base_url}{sep}ahkHomeReset={int(time.time() * 1000)}")
        self.home_resetting = False
        self.update_exam_bar()

    # ---------- keluar ----------
    def trigger_exit(self) -> None:
        if self.exiting_dialog:
            return
        self.exiting_dialog = True
        QApplication.beep()
        ans = self._msgbox(
            QMessageBox.Icon.Warning, "CakBro - Konfirmasi Keluar",
            "Apakah Anda yakin ingin keluar dari aplikasi?\n\nTekan [Yes] untuk keluar dari ujian.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ans == QMessageBox.StandardButton.Yes:
            self.quit_app()
            return
        self.exiting_dialog = False

    # ---------- timer keamanan ----------
    def security_check(self) -> None:
        if not DEV:
            set_taskbar_visible(False)
            close_explorer_windows()

    def kill_apps_tick(self) -> None:
        if self.killer_enabled:
            kill_forbidden_apps()

    def keep_focus(self) -> None:
        if self.exiting or self.exiting_dialog or self.home_resetting or DEV:
            return
        if self.overlay.isVisible():
            self.overlay.raise_()
        active = self.app.activeWindow()
        if active is None:
            if self.popups and self.popups[-1][0].isVisible():
                self.popups[-1][0].activateWindow()
            else:
                self.raise_()
                self.activateWindow()
        geo = self.app.primaryScreen().geometry()
        if not IS_MAC and not self.isFullScreen():
            self.showFullScreen()
        elif IS_MAC and self.geometry() != geo:
            self.setGeometry(geo)


# ============================================================
# SPLASH
# ============================================================
def make_splash(app: QApplication) -> QWidget:
    w = QWidget()
    w.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
    w.setStyleSheet("background:#0d1117;border:1px solid #00d4ff;")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(20, 22, 20, 18)
    for text, size, bold, color in (
            ("CakBro", 28, True, "#00d4ff"),
            ("Safe Exam Browser", 11, False, "#cccccc"),
            ("Mempersiapkan lingkungan ujian yang aman...", 9, False, "#666666"),
            (f"v{APP_VERSION} -- Powered by Pakkar", 8, False, "#444444")):
        lb = QLabel(text)
        lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = QFont("Segoe UI", size)
        f.setBold(bold)
        lb.setFont(f)
        lb.setStyleSheet(f"color:{color};border:none;")
        lay.addWidget(lb)
    w.setFixedSize(500, 218)
    geo = app.primaryScreen().geometry()
    w.move(geo.x() + (geo.width() - 500) // 2, geo.y() + (geo.height() - 218) // 2)
    return w


# ============================================================
# MAIN
# ============================================================
def main() -> int:
    app = QApplication([a for a in sys.argv if a != "--dev"])
    app.setApplicationName(APP_TITLE)
    app.setQuitOnLastWindowClosed(False)

    lock = QLockFile(os.path.join(tempfile.gettempdir(), "cakbro.lock"))
    if not lock.tryLock(100):
        print("CakBro sudah berjalan.", file=sys.stderr)
        return 1

    splash = make_splash(app)
    splash.show()
    app.processEvents()
    if check_for_update():       # updater berjalan; keluar tanpa membuka kiosk
        return 0
    end = time.monotonic() + 2.5
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    splash.close()

    kiosk = Kiosk(app)
    atexit.register(lambda: (set_taskbar_visible(True), mac_lockdown(False)))
    app.aboutToQuit.connect(kiosk.full_cleanup)
    kiosk.start()
    try:
        return app.exec()
    finally:
        kiosk.full_cleanup()
        lock.unlock()


if __name__ == "__main__":
    sys.exit(main())
