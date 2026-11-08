# -*- coding: utf-8 -*-
r"""
==============================================================================
 Key Macro Studio  - Windows キー操作 自動作成＆実行ソフト
------------------------------------------------------------------------------
 Version : 1.2
 File    : key_macro_studio_v1.2.py
 Python  : 3.8 以上 (標準ライブラリのみ / 追加インストール不要)
 OS      : Windows 10 / 11  (他OSでは「ドライラン(模擬実行)」のみ動作)
------------------------------------------------------------------------------
 概要
   ・コマンド(キー入力・待機・アプリ起動・ウィンドウ選択…)を並べてマクロを作成
   ・LOOP / WHILE / IF-ELSE / 変数(SET) / 式 によって「アルゴリズム」として組める
   ・ブロック編集(GUI) と スクリプト編集(テキスト) を相互変換
   ・.kmacro(JSON) で保存・読込
   ・F12 で緊急停止
 起動
   python key_macro_studio_v1.2.py
 exe 化 (PyInstaller / 各自で実施)
   pip install pyinstaller
   pyinstaller --onefile --noconsole --noupx --name KeyMacroStudio_v1.2 key_macro_studio_v1.2.py
   → dist\KeyMacroStudio_v1.2.exe
 データの保存先
   exe/py と同じフォルダ (書き込めない場合は %APPDATA%\KeyMacroStudio)
     key_macro_studio_settings.json  … 画面設定・最後に開いたファイル
     macros\                         … マクロ保存の既定フォルダ
     key_macro_studio_error.log      … 予期しないエラーの記録
------------------------------------------------------------------------------
 変更履歴
   v1.2  exe 化・GitHub 配布向けに改修
         ・保存先を exe/py と同じフォルダ基準に統一 (sys.frozen 判定)
         ・設定の自動保存/復元、マクロ既定フォルダ、エラーログ
         ・アプリ起動を --noconsole でも動くよう修正 (標準入出力を明示、
           CREATE_NO_WINDOW / CREATE_NEW_CONSOLE を指定)
         ・アプリ起動に「ウィンドウ show/hide」設定を追加
   v1.1  UI を近未来 HUD デザインに刷新(ダーク+ネオン、アニメーションヘッダー、
         状態インジケーター、発光ボタン、ターミナル風ログ)
   v1.0  初版
==============================================================================
"""

import ast
import json
import operator
import os
import queue
import random
import re
import shlex
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_NAME = "Key Macro Studio"
APP_VERSION = "1.2"
FILE_EXT = ".kmacro"
IS_WIN = sys.platform == "win32"
MAX_STEPS = 1_000_000          # 無限ループ保護(実行ステップ上限)

# --noconsole の exe では sys.stdout/stderr が None になるため、念のため差し替える
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")


# ============================================================================
#  0. 保存先フォルダ (exe 化対応)
#     exe 実行時 : exe と同じフォルダ (sys.executable 基準。__file__ は一時展開先を指すため使わない)
#     .py 実行時 : スクリプトと同じフォルダ
#     書き込み不可 (Program Files 等) の場合は %APPDATA%\KeyMacroStudio へ退避
# ============================================================================
def _app_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _is_writable(d):
    try:
        os.makedirs(d, exist_ok=True)
        test = os.path.join(d, ".kms_write_test")
        with open(test, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(test)
        return True
    except OSError:
        return False


def _data_dir():
    base = _app_base_dir()
    if _is_writable(base):
        return base
    fallback = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "KeyMacroStudio")
    os.makedirs(fallback, exist_ok=True)
    return fallback


APP_DIR = _app_base_dir()
DATA_DIR = _data_dir()
SETTINGS_FILE = os.path.join(DATA_DIR, "key_macro_studio_settings.json")
MACRO_DIR = os.path.join(DATA_DIR, "macros")
ERROR_LOG = os.path.join(DATA_DIR, "key_macro_studio_error.log")


def write_error_log(text):
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {APP_NAME} v{APP_VERSION}\n{text}\n\n")
    except OSError:
        pass


def popen_kwargs(hidden=False, cwd=None):
    """--noconsole の exe でも安全に子プロセスを起動するための共通設定。"""
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
          "stderr": subprocess.DEVNULL, "close_fds": True}
    if cwd and os.path.isdir(cwd):
        kw["cwd"] = cwd
    if IS_WIN:
        # hide: コンソールを出さない / show: コンソールアプリは専用ウィンドウで表示
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW if hidden else subprocess.CREATE_NEW_CONSOLE
    return kw


# ============================================================================
#  1. Windows API ラッパー (ctypes / SendInput)
# ============================================================================
VK = {
    "BACKSPACE": 0x08, "BS": 0x08, "TAB": 0x09, "ENTER": 0x0D, "RETURN": 0x0D,
    "SHIFT": 0x10, "CTRL": 0x11, "CONTROL": 0x11, "ALT": 0x12, "PAUSE": 0x13,
    "CAPSLOCK": 0x14, "ESC": 0x1B, "ESCAPE": 0x1B, "SPACE": 0x20,
    "PAGEUP": 0x21, "PGUP": 0x21, "PAGEDOWN": 0x22, "PGDN": 0x22,
    "END": 0x23, "HOME": 0x24, "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27,
    "DOWN": 0x28, "PRINTSCREEN": 0x2C, "INSERT": 0x2D, "INS": 0x2D,
    "DELETE": 0x2E, "DEL": 0x2E, "WIN": 0x5B, "LWIN": 0x5B, "RWIN": 0x5C,
    "APPS": 0x5D, "MENU": 0x5D,
    "NUM0": 0x60, "NUM1": 0x61, "NUM2": 0x62, "NUM3": 0x63, "NUM4": 0x64,
    "NUM5": 0x65, "NUM6": 0x66, "NUM7": 0x67, "NUM8": 0x68, "NUM9": 0x69,
    "MULTIPLY": 0x6A, "ADD": 0x6B, "SUBTRACT": 0x6D, "DECIMAL": 0x6E,
    "DIVIDE": 0x6F, "NUMLOCK": 0x90, "SCROLLLOCK": 0x91,
    "VOLUMEMUTE": 0xAD, "VOLUMEDOWN": 0xAE, "VOLUMEUP": 0xAF,
    "MEDIANEXT": 0xB0, "MEDIAPREV": 0xB1, "MEDIASTOP": 0xB2, "MEDIAPLAY": 0xB3,
    "KANJI": 0x19, "HANKAKU": 0x19, "CONVERT": 0x1C, "NONCONVERT": 0x1D,
}
for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
    VK[_c] = ord(_c)
for _i in range(1, 25):
    VK[f"F{_i}"] = 0x6F + _i
# 拡張キー(EXTENDEDKEY フラグが必要)
EXTENDED_VK = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E,
               0x5B, 0x5C, 0x5D, 0x6F, 0x90, 0x2C}
VK_F12 = 0x7B


def key_to_vk(name):
    n = str(name).strip().upper()
    if n in VK:
        return VK[n]
    if len(n) == 1:
        return None                      # 記号などは TYPE で送る
    raise ValueError(f"不明なキー名: {name}")


class WinAPI:
    """SendInput などのラッパー。Windows 以外では何もしない(模擬)。"""

    def __init__(self):
        self.ok = IS_WIN
        if not IS_WIN:
            return
        import ctypes
        from ctypes import wintypes
        self.ct = ctypes
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        self.u32 = u32
        try:  # 高DPI環境でクリック座標がずれないように
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        ULONG_PTR = ctypes.c_size_t

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ULONG_PTR)]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                        ("wParamH", wintypes.WORD)]

        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        self.INPUT, self.KEYBDINPUT, self.MOUSEINPUT = INPUT, KEYBDINPUT, MOUSEINPUT
        u32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        u32.SendInput.restype = wintypes.UINT
        u32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
        u32.MapVirtualKeyW.restype = wintypes.UINT
        u32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
        u32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        u32.IsWindowVisible.argtypes = (wintypes.HWND,)
        u32.IsIconic.argtypes = (wintypes.HWND,)
        u32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        u32.SetForegroundWindow.argtypes = (wintypes.HWND,)
        u32.GetForegroundWindow.restype = wintypes.HWND
        u32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
        u32.GetAsyncKeyState.restype = ctypes.c_short
        u32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
        self.WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self.wintypes = wintypes

    # ---- キーボード -------------------------------------------------------
    def _send_kbd(self, vk=0, scan=0, flags=0):
        inp = self.INPUT(type=1)
        inp.ki = self.KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
        self.u32.SendInput(1, self.ct.byref(inp), self.ct.sizeof(inp))

    def key_down(self, vk):
        if not self.ok:
            return
        flags = 0x0001 if vk in EXTENDED_VK else 0
        self._send_kbd(vk, self.u32.MapVirtualKeyW(vk, 0), flags)

    def key_up(self, vk):
        if not self.ok:
            return
        flags = 0x0002 | (0x0001 if vk in EXTENDED_VK else 0)
        self._send_kbd(vk, self.u32.MapVirtualKeyW(vk, 0), flags)

    def type_char(self, ch):
        """Unicode 文字を直接送信(日本語もOK)。"""
        if not self.ok:
            return
        data = ch.encode("utf-16-le")
        for i in range(0, len(data), 2):
            code = int.from_bytes(data[i:i + 2], "little")
            self._send_kbd(0, code, 0x0004)            # KEYEVENTF_UNICODE
            self._send_kbd(0, code, 0x0004 | 0x0002)   # + KEYUP

    def is_pressed(self, vk):
        if not self.ok:
            return False
        return bool(self.u32.GetAsyncKeyState(vk) & 0x8000)

    # ---- マウス -----------------------------------------------------------
    def click(self, x, y, button="left", clicks=1):
        if not self.ok:
            return
        self.u32.SetCursorPos(int(x), int(y))
        down, up = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010),
                    "middle": (0x0020, 0x0040)}.get(button, (0x0002, 0x0004))
        for _ in range(int(clicks)):
            for f in (down, up):
                inp = self.INPUT(type=0)
                inp.mi = self.MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=f, time=0, dwExtraInfo=0)
                self.u32.SendInput(1, self.ct.byref(inp), self.ct.sizeof(inp))
            time.sleep(0.05)

    def cursor_pos(self):
        if not self.ok:
            return (0, 0)
        pt = self.wintypes.POINT()
        self.u32.GetCursorPos(self.ct.byref(pt))
        return (pt.x, pt.y)

    # ---- ウィンドウ -------------------------------------------------------
    def list_windows(self):
        if not self.ok:
            return []
        result = []

        def cb(hwnd, _lp):
            if self.u32.IsWindowVisible(hwnd):
                n = self.u32.GetWindowTextLengthW(hwnd)
                if n > 0:
                    buf = self.ct.create_unicode_buffer(n + 1)
                    self.u32.GetWindowTextW(hwnd, buf, n + 1)
                    result.append((hwnd, buf.value))
            return True
        self.u32.EnumWindows(self.WNDENUMPROC(cb), 0)
        return result

    def find_window(self, title_part):
        key = str(title_part).lower()
        for hwnd, title in self.list_windows():
            if key in title.lower():
                return hwnd
        return None

    def activate(self, hwnd):
        if not self.ok or not hwnd:
            return False
        if self.u32.IsIconic(hwnd):
            self.u32.ShowWindow(hwnd, 9)        # SW_RESTORE
        # フォアグラウンドロック回避の定番手法(ALT を一瞬押す)
        self.key_down(VK["ALT"])
        self.key_up(VK["ALT"])
        ok = bool(self.u32.SetForegroundWindow(hwnd))
        return ok


WIN = WinAPI()


# ============================================================================
#  2. コマンド定義 (ここに追加すれば GUI / スクリプト / 実行 すべてに反映)
# ============================================================================
#  fields: (キー, 表示名, 既定値, ヒント)
CMD_DEFS = {
    "LAUNCH":      {"label": "アプリ起動", "cat": "app",
                    "fields": [("path", "実行ファイル", "notepad.exe", "例: C:\\Program Files\\xxx\\app.exe"),
                               ("args", "引数", "", "コマンドライン引数(任意)"),
                               ("window", "ウィンドウ", "show", "show=表示 / hide=非表示(コンソールアプリを裏で動かす時)")],
                    "fmt": "{path} {args}"},
    "ACTIVATE":    {"label": "ウィンドウ選択", "cat": "app",
                    "fields": [("title", "タイトル(部分一致)", "メモ帳", ""),
                               ("timeout", "待ち時間(秒)", "5", "見つかるまで待つ秒数")],
                    "fmt": "「{title}」を前面へ"},
    "WAIT_WINDOW": {"label": "ウィンドウ待ち", "cat": "app",
                    "fields": [("title", "タイトル(部分一致)", "メモ帳", ""),
                               ("timeout", "タイムアウト(秒)", "10", "")],
                    "fmt": "「{title}」が出るまで 最大{timeout}秒"},
    "KEY":         {"label": "キー押下", "cat": "key",
                    "fields": [("key", "キー名", "ENTER", "ENTER / TAB / F5 / A / DOWN ..."),
                               ("count", "回数(式可)", "1", "")],
                    "fmt": "[{key}] × {count}"},
    "HOTKEY":      {"label": "同時押し", "cat": "key",
                    "fields": [("keys", "キー組合せ", "CTRL+S", "例: CTRL+SHIFT+ESC / ALT+F4")],
                    "fmt": "[{keys}]"},
    "TYPE":        {"label": "文字入力", "cat": "key",
                    "fields": [("text", "テキスト", "Hello", "${式} で変数埋込 / \\n で改行")],
                    "fmt": "\"{text}\""},
    "CLICK":       {"label": "クリック", "cat": "key",
                    "fields": [("x", "X座標(式可)", "100", ""), ("y", "Y座標(式可)", "100", ""),
                               ("button", "ボタン", "left", "left / right / middle"),
                               ("clicks", "回数", "1", "2 でダブルクリック")],
                    "fmt": "({x}, {y}) {button}×{clicks}"},
    "WAIT":        {"label": "待機", "cat": "wait",
                    "fields": [("sec", "秒(式可)", "1.0", "")],
                    "fmt": "{sec} 秒"},
    "SET":         {"label": "変数設定", "cat": "var",
                    "fields": [("name", "変数名", "n", ""), ("expr", "値(式)", "0", "例: n + 1 / randint(1,6)")],
                    "fmt": "{name} = {expr}"},
    "LOOP":        {"label": "繰り返し(回数)", "cat": "block",
                    "fields": [("count", "回数(式可)", "3", ""), ("var", "カウンタ変数", "i", "1 から始まる")],
                    "fmt": "{count} 回  ({var} = 1..)"},
    "WHILE":       {"label": "繰り返し(条件)", "cat": "block",
                    "fields": [("cond", "条件式", "n < 10", "")],
                    "fmt": "{cond} の間"},
    "IF":          {"label": "条件分岐", "cat": "block",
                    "fields": [("cond", "条件式", "window_exists('メモ帳')", "")],
                    "fmt": "もし {cond}"},
    "ELSE":        {"label": "それ以外", "cat": "block", "fields": [], "fmt": ""},
    "END":         {"label": "ブロック終了", "cat": "block", "fields": [], "fmt": ""},
    "LOG":         {"label": "ログ出力", "cat": "var",
                    "fields": [("message", "メッセージ", "i = ${i}", "")],
                    "fmt": "{message}"},
    "STOP":        {"label": "マクロ終了", "cat": "var", "fields": [], "fmt": ""},
    "COMMENT":     {"label": "コメント", "cat": "comment",
                    "fields": [("text", "メモ", "", "")],
                    "fmt": "{text}"},
}
BLOCK_START = ("LOOP", "WHILE", "IF")
PALETTE = [
    ("アプリ", ["LAUNCH", "WAIT_WINDOW", "ACTIVATE"]),
    ("入力", ["KEY", "HOTKEY", "TYPE", "CLICK"]),
    ("制御", ["WAIT", "LOOP", "WHILE", "IF", "ELSE", "END"]),
    ("その他", ["SET", "LOG", "STOP", "COMMENT"]),
]
# ---- 近未来テーマ(カラーパレット) -------------------------------------------
C = {
    "bg": "#060a13", "grid": "#0b1424", "panel": "#0b1220", "panel_alt": "#0d1628",
    "panel2": "#111c31", "term": "#03060c", "line": "#1a2a45", "line2": "#2a4066",
    "cyan": "#00e5ff", "cyan_mid": "#5fd8ee", "cyan_dim": "#0b5d70", "mag": "#ff2bd6",
    "text": "#d6ecff", "dim": "#6a87a8", "ok": "#39ff9c", "warn": "#ffcc33",
    "err": "#ff4d6d", "sel": "#063a4c", "run_bg": "#33103a",
}
CAT_COLOR = {"app": "#33b5ff", "key": "#e3f2ff", "wait": "#ffcc33",
             "var": "#b98cff", "block": "#ff4d8d", "comment": "#39ff9c"}
PALETTE_UI = [("APP", "アプリ", PALETTE[0][1]), ("INPUT", "入力", PALETTE[1][1]),
              ("CONTROL", "制御", PALETTE[2][1]), ("UTILITY", "その他", PALETTE[3][1])]


def blend(c1, c2, t):
    """2色を t (0..1) で混ぜる。"""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def new_cmd(ctype):
    d = {"type": ctype}
    for key, _lbl, default, _h in CMD_DEFS[ctype]["fields"]:
        d[key] = default
    return d


def cmd_summary(c):
    d = CMD_DEFS.get(c["type"])
    if not d:
        return "?"
    vals = {k: c.get(k, "") for k, *_ in d["fields"]}
    try:
        return d["fmt"].format(**vals).strip()
    except Exception:
        return ""


def calc_depths(cmds):
    """インデント表示用の深さ(構造エラーがあっても落ちない)。"""
    depths, d = [], 0
    for c in cmds:
        t = c["type"]
        if t in ("END", "ELSE"):
            d = max(0, d - 1)
        depths.append(d)
        if t in BLOCK_START or t == "ELSE":
            d += 1
    return depths


# ============================================================================
#  3. スクリプト(テキスト) ⇔ コマンド 相互変換
#     書式:  COMMAND key=value key="空白を含む値"   (# 行はコメント)
#     位置引数も可:  KEY ENTER 3   /   WAIT 0.5
# ============================================================================
def to_script(cmds):
    lines = [f"# {APP_NAME} v{APP_VERSION} script"]
    for c, depth in zip(cmds, calc_depths(cmds)):
        ind = "    " * depth
        if c["type"] == "COMMENT":
            lines.append(f"{ind}# {c.get('text', '')}")
            continue
        parts = [c["type"]]
        for key, *_ in CMD_DEFS[c["type"]]["fields"]:
            v = str(c.get(key, ""))
            if (key == "args" and v == "") or (key == "window" and v in ("", "show")):
                continue
            parts.append(f"{key}={shlex.quote(v)}")
        lines.append(ind + " ".join(parts))
    return "\n".join(lines) + "\n"


def from_script(text):
    cmds = []
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            body = line[1:].strip()
            if body.startswith(f"{APP_NAME} v"):
                continue                       # ヘッダ行は無視
            cmds.append({"type": "COMMENT", "text": body})
            continue
        try:
            toks = shlex.split(line, posix=True)
        except ValueError as e:
            raise ValueError(f"{no}行目: 引用符の対応が不正です ({e})")
        ctype = toks[0].upper()
        if ctype not in CMD_DEFS:
            raise ValueError(f"{no}行目: 不明なコマンド「{toks[0]}」")
        c = new_cmd(ctype)
        keys = [k for k, *_ in CMD_DEFS[ctype]["fields"]]
        pos = 0
        for tok in toks[1:]:
            if "=" in tok and tok.split("=", 1)[0] in keys:
                k, v = tok.split("=", 1)
                c[k] = v
            else:
                if pos >= len(keys):
                    raise ValueError(f"{no}行目: 引数が多すぎます「{tok}」")
                c[keys[pos]] = tok
                pos += 1
        cmds.append(c)
    return cmds


# ============================================================================
#  4. 安全な式評価器 (eval を使わず AST を解釈)
# ============================================================================
class SafeEval:
    BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
           ast.Mod: operator.mod, ast.Pow: operator.pow}
    CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
           ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
           ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}

    def __init__(self, variables, funcs):
        self.vars, self.funcs = variables, funcs

    def eval(self, expr):
        s = str(expr).strip()
        if s == "":
            raise ValueError("式が空です")
        return self._ev(ast.parse(s, mode="eval").body)

    def _ev(self, n):
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            if n.id in self.vars:
                return self.vars[n.id]
            if n.id in ("true", "false"):
                return n.id == "true"
            raise NameError(f"未定義の変数「{n.id}」")
        if isinstance(n, ast.BinOp) and type(n.op) in self.BIN:
            a, b = self._ev(n.left), self._ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 100:
                raise ValueError("べき乗が大きすぎます")
            return self.BIN[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp):
            v = self._ev(n.operand)
            if isinstance(n.op, ast.USub):
                return -v
            if isinstance(n.op, ast.UAdd):
                return +v
            if isinstance(n.op, ast.Not):
                return not v
        if isinstance(n, ast.BoolOp):
            if isinstance(n.op, ast.And):
                r = True
                for v in n.values:
                    r = self._ev(v)
                    if not r:
                        return r
                return r
            r = False
            for v in n.values:
                r = self._ev(v)
                if r:
                    return r
            return r
        if isinstance(n, ast.Compare):
            left = self._ev(n.left)
            for op, comp in zip(n.ops, n.comparators):
                right = self._ev(comp)
                if not self.CMP[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(n, ast.IfExp):
            return self._ev(n.body) if self._ev(n.test) else self._ev(n.orelse)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and not n.keywords:
            f = self.funcs.get(n.func.id)
            if f is None:
                raise NameError(f"使えない関数「{n.func.id}」")
            return f(*[self._ev(a) for a in n.args])
        if isinstance(n, (ast.List, ast.Tuple)):
            return [self._ev(e) for e in n.elts]
        if isinstance(n, ast.Subscript):
            return self._ev(n.value)[self._ev(n.slice)]
        raise ValueError(f"この式は使えません: {ast.dump(n)[:40]}")


# ============================================================================
#  5. 実行エンジン
# ============================================================================
class MacroError(Exception):
    pass


class StopRequested(Exception):
    pass


def build_blocks(cmds):
    """LOOP/WHILE/IF ⇔ ELSE ⇔ END の対応表を作成(構文チェックを兼ねる)。"""
    j, stack = {}, []
    for i, c in enumerate(cmds):
        t = c["type"]
        if t in BLOCK_START:
            stack.append(i)
            j[i] = {}
        elif t == "ELSE":
            if not stack or cmds[stack[-1]]["type"] != "IF":
                raise MacroError(f"{i + 1}行目: ELSE に対応する IF がありません")
            if "else" in j[stack[-1]]:
                raise MacroError(f"{i + 1}行目: 1つの IF に ELSE が2つあります")
            j[stack[-1]]["else"] = i
            j[i] = {"start": stack[-1]}
        elif t == "END":
            if not stack:
                raise MacroError(f"{i + 1}行目: END に対応するブロックがありません")
            s = stack.pop()
            j[s]["end"] = i
            j[i] = {"start": s}
            if "else" in j[s]:
                j[j[s]["else"]]["end"] = i
    if stack:
        s = stack[-1]
        raise MacroError(f"{s + 1}行目: {cmds[s]['type']} に対応する END がありません")
    return j


FUNC_TEXT = ("window_exists('タイトル')\nkey_pressed('F11')\nrandint(a,b)   now()\n"
             "int() float() str() len()\nabs() min() max() round()")
FUNC_HELP = ("window_exists('タイトル')  randint(a,b)  int()  float()  str()  "
             "len()  abs()  min()  max()  round()  now()  key_pressed('F11')")


class MacroRunner(threading.Thread):
    def __init__(self, cmds, settings, out_q, dry_run=False):
        super().__init__(daemon=True)
        self.cmds = [dict(c) for c in cmds]
        self.start_delay = float(settings.get("start_delay", 3))
        self.interval = float(settings.get("key_interval_ms", 50)) / 1000.0
        self.q = out_q
        self.dry = dry_run or not IS_WIN
        self.stop_event = threading.Event()
        self.vars = {}
        self.funcs = {
            "window_exists": lambda t: (WIN.find_window(t) is not None) if not self.dry else True,
            "key_pressed": lambda k: WIN.is_pressed(key_to_vk(k) or 0),
            "randint": random.randint, "int": int, "float": float, "str": str,
            "len": len, "abs": abs, "min": min, "max": max, "round": round,
            "now": lambda: time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.ev = SafeEval(self.vars, self.funcs)
        self.pc = 0

    # ---- ユーティリティ ---------------------------------------------------
    def log(self, msg, level="info"):
        self.q.put(("log", level, msg))

    def stop(self):
        self.stop_event.set()

    def check_stop(self):
        if self.stop_event.is_set():
            raise StopRequested()
        if not self.dry and WIN.is_pressed(VK_F12):
            self.stop_event.set()
            raise StopRequested()

    def sleep(self, sec):
        end = time.time() + max(0.0, sec)
        while True:
            self.check_stop()
            rest = end - time.time()
            if rest <= 0:
                return
            time.sleep(min(0.03, rest))

    def sub(self, text):
        """テキスト内の ${式} を評価結果で置換。"""
        return re.sub(r"\$\{(.+?)\}", lambda m: str(self.ev.eval(m.group(1))), str(text))

    def num(self, expr):
        return self.ev.eval(self.sub(expr))

    # ---- 実行本体 ---------------------------------------------------------
    def run(self):
        try:
            jumps = build_blocks(self.cmds)
            mode = "ドライラン(模擬)" if self.dry else "本番実行"
            self.log(f"▶ 開始 [{mode}]  {self.start_delay:.1f} 秒後にスタート (F12で緊急停止)", "head")
            self.sleep(self.start_delay)
            self._execute(jumps)
            self.log("■ 正常終了", "head")
        except StopRequested:
            self.log("■ 停止しました", "warn")
        except MacroError as e:
            self.log(f"✖ 構文エラー: {e}", "error")
        except Exception as e:
            self.log(f"✖ {self.pc + 1}行目でエラー: {e}", "error")
        finally:
            self.q.put(("done",))

    def _execute(self, jumps):
        cmds, loops, steps = self.cmds, {}, 0
        self.pc = 0
        while 0 <= self.pc < len(cmds):
            self.check_stop()
            steps += 1
            if steps > MAX_STEPS:
                raise MacroError(f"実行ステップが上限({MAX_STEPS})を超えました。無限ループの可能性")
            c = cmds[self.pc]
            t = c["type"]
            self.q.put(("pc", self.pc))
            nxt = self.pc + 1

            if t == "LOOP":
                cnt = int(self.num(c.get("count", "1")))
                if cnt <= 0:
                    nxt = jumps[self.pc]["end"] + 1
                else:
                    loops[self.pc] = [1, cnt]
                    if c.get("var"):
                        self.vars[c["var"]] = 1
            elif t == "WHILE":
                if not self.num(c.get("cond", "False")):
                    nxt = jumps[self.pc]["end"] + 1
            elif t == "IF":
                if not self.num(c.get("cond", "False")):
                    j = jumps[self.pc]
                    nxt = (j["else"] + 1) if "else" in j else (j["end"] + 1)
            elif t == "ELSE":
                nxt = jumps[self.pc]["end"] + 1
            elif t == "END":
                s = jumps[self.pc]["start"]
                st = cmds[s]["type"]
                if st == "LOOP":
                    cur = loops[s]
                    cur[0] += 1
                    if cur[0] <= cur[1]:
                        if cmds[s].get("var"):
                            self.vars[cmds[s]["var"]] = cur[0]
                        nxt = s + 1
                    else:
                        del loops[s]
                elif st == "WHILE":
                    nxt = s                       # 条件を再評価
            else:
                if self._do(c) == "STOP":
                    self.log("STOP コマンドで終了", "warn")
                    return
            self.pc = nxt

    def _do(self, c):
        t, dry = c["type"], self.dry
        tag = "[模擬] " if dry else ""
        if t == "COMMENT":
            return
        if t == "LAUNCH":
            path, args = self.sub(c.get("path", "")), self.sub(c.get("args", ""))
            self.log(f"{tag}起動: {path} {args}")
            if not dry:
                hidden = str(c.get("window", "show")).strip().lower() == "hide"
                cwd = os.path.dirname(path) if os.path.isabs(path) else None
                is_prog = os.path.splitext(path)[1].lower() in (".exe", ".bat", ".cmd", ".com", "")
                if args or hidden or is_prog:
                    cmd = [path] + (shlex.split(args, posix=False) if args else [])
                    subprocess.Popen(cmd, **popen_kwargs(hidden, cwd))
                else:
                    os.startfile(path)                    # 文書・URL などは関連付けで開く
        elif t in ("ACTIVATE", "WAIT_WINDOW"):
            title = self.sub(c.get("title", ""))
            timeout = float(self.num(c.get("timeout", "5")))
            if dry:
                self.log(f"{tag}ウィンドウ「{title}」{'を前面へ' if t == 'ACTIVATE' else 'を待機'}")
                return
            end, hwnd = time.time() + timeout, None
            while time.time() <= end:
                hwnd = WIN.find_window(title)
                if hwnd:
                    break
                self.sleep(0.2)
            if not hwnd:
                raise MacroError(f"ウィンドウ「{title}」が {timeout} 秒以内に見つかりません")
            if t == "ACTIVATE":
                WIN.activate(hwnd)
                self.sleep(0.2)
                self.log(f"前面化: {title}")
            else:
                self.log(f"検出: {title}")
        elif t == "KEY":
            name = self.sub(c.get("key", "")).strip()
            cnt = int(self.num(c.get("count", "1")))
            vk = key_to_vk(name)
            self.log(f"{tag}キー [{name}] × {cnt}")
            for _ in range(cnt):
                self.check_stop()
                if vk is None:
                    WIN.type_char(name)
                else:
                    WIN.key_down(vk)
                    WIN.key_up(vk)
                self.sleep(self.interval)
        elif t == "HOTKEY":
            combo = self.sub(c.get("keys", ""))
            vks = [key_to_vk(k) for k in combo.split("+") if k.strip()]
            if None in vks:
                raise MacroError(f"同時押しに使えないキーがあります: {combo}")
            self.log(f"{tag}同時押し [{combo}]")
            for v in vks:
                WIN.key_down(v)
                time.sleep(0.02)
            for v in reversed(vks):
                WIN.key_up(v)
                time.sleep(0.02)
            self.sleep(self.interval)
        elif t == "TYPE":
            text = self.sub(c.get("text", "")).replace("\\n", "\n").replace("\\t", "\t")
            self.log(f"{tag}入力: {text!r}")
            for ch in text:
                self.check_stop()
                if ch == "\n":
                    WIN.key_down(VK["ENTER"])
                    WIN.key_up(VK["ENTER"])
                elif ch == "\t":
                    WIN.key_down(VK["TAB"])
                    WIN.key_up(VK["TAB"])
                else:
                    WIN.type_char(ch)
                time.sleep(min(self.interval, 0.02))
        elif t == "CLICK":
            x, y = int(self.num(c.get("x", "0"))), int(self.num(c.get("y", "0")))
            btn, n = c.get("button", "left"), int(self.num(c.get("clicks", "1")))
            self.log(f"{tag}クリック ({x},{y}) {btn}×{n}")
            WIN.click(x, y, btn, n)
            self.sleep(self.interval)
        elif t == "WAIT":
            sec = float(self.num(c.get("sec", "1")))
            self.log(f"{tag}待機 {sec} 秒")
            self.sleep(sec)
        elif t == "SET":
            name = c.get("name", "").strip()
            if not name.isidentifier():
                raise MacroError(f"変数名が不正です: {name}")
            self.vars[name] = self.num(c.get("expr", "0"))
            self.log(f"変数 {name} = {self.vars[name]!r}")
        elif t == "LOG":
            self.log("💬 " + self.sub(c.get("message", "")), "user")
        elif t == "STOP":
            return "STOP"


# ============================================================================
#  6. GUI
# ============================================================================
SAMPLE = [
    {"type": "COMMENT", "text": "サンプル: メモ帳を開いて連番と偶数/奇数を自動入力"},
    {"type": "LAUNCH", "path": "notepad.exe", "args": "", "window": "show"},
    {"type": "WAIT_WINDOW", "title": "メモ帳", "timeout": "10"},
    {"type": "ACTIVATE", "title": "メモ帳", "timeout": "5"},
    {"type": "SET", "name": "n", "expr": "5"},
    {"type": "LOOP", "count": "n", "var": "i"},
    {"type": "TYPE", "text": "${i}行目: 値=${i*10} "},
    {"type": "IF", "cond": "i % 2 == 0"},
    {"type": "TYPE", "text": "(偶数)"},
    {"type": "ELSE"},
    {"type": "TYPE", "text": "(奇数)"},
    {"type": "END"},
    {"type": "KEY", "key": "ENTER", "count": "1"},
    {"type": "WAIT", "sec": "0.2"},
    {"type": "END"},
    {"type": "HOTKEY", "keys": "CTRL+HOME"},
    {"type": "LOG", "message": "完了: ${n} 行入力しました (${now()})"},
]


class NeonButton(tk.Frame):
    """発光枠ボタン。filled=True で塗りつぶし(主操作用)。"""

    def __init__(self, master, text, command=None, color=None, filled=False, width=None,
                 font=None, padx=12, pady=5, anchor="center"):
        self.color = color or C["cyan"]
        self.filled, self.command, self.enabled = filled, command, True
        super().__init__(master, bg=C["line"], padx=1, pady=1, cursor="hand2")
        self.lbl = tk.Label(self, text=text, font=font, anchor=anchor, padx=padx, pady=pady,
                            width=width if width else 0)
        self.lbl.pack(fill="both", expand=True)
        for w in (self, self.lbl):
            w.bind("<Enter>", lambda e: self._paint(True))
            w.bind("<Leave>", lambda e: self._paint(False))
            w.bind("<ButtonRelease-1>", self._click)
        self._paint(False)

    def _paint(self, hover):
        if not self.enabled:
            self.configure(bg=C["line"], cursor="arrow")
            self.lbl.configure(bg=C["panel"], fg=C["line2"], cursor="arrow")
            return
        self.configure(cursor="hand2")
        self.lbl.configure(cursor="hand2")
        if self.filled:
            self.configure(bg=self.color)
            self.lbl.configure(bg=blend(self.color, "#ffffff", 0.25) if hover else self.color, fg="#02131a")
        else:
            self.configure(bg=self.color if hover else blend(self.color, C["bg"], 0.55))
            self.lbl.configure(bg=blend(self.color, C["panel2"], 0.82) if hover else C["panel2"],
                               fg=self.color)

    def _click(self, _e=None):
        if self.enabled and self.command:
            self.command()

    def set_enabled(self, on):
        self.enabled = bool(on)
        self._paint(False)


class App(tk.Tk):
    """近未来 HUD スタイルの GUI (v1.1)。"""

    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME}  v{APP_VERSION}")
        self.geometry("1280x820")
        self.minsize(1040, 640)
        self.configure(bg=C["bg"])
        self.cmds = [dict(c) for c in SAMPLE]
        self.filepath = None
        self.runner = None
        self.q = queue.Queue()
        self.prop_widgets = {}
        self.tab_index = 0
        self.var_delay = tk.DoubleVar(value=3.0)
        self.var_interval = tk.IntVar(value=50)
        self.var_dry = tk.BooleanVar(value=not IS_WIN)
        self.var_minimize = tk.BooleanVar(value=True)
        self.run_state = "STANDBY"
        self.report_callback_exception = self._on_tk_error
        self._prefs = self._load_prefs()
        self._scan_x = 0
        self._pulse = 0
        self._fonts()
        self._style()
        self._build()
        self.refresh_tree()
        self.show_props()
        self.log_line(f"{APP_NAME} v{APP_VERSION} 起動完了 ― SYSTEM ONLINE", "head")
        self.log_line(f"データ保存先: {DATA_DIR}")
        last = self._prefs.get("last_file")
        if last and os.path.isfile(last):
            self.load_file(last)
        self._closing = False
        self.after(60, self._poll)
        self.after(40, self._animate)
        self.bind("<Control-s>", lambda e: self.save())
        self.bind("<Control-o>", lambda e: self.open())
        self.bind("<F5>", lambda e: self.run_macro())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- フォント / ttk スタイル -------------------------------------------
    def _fonts(self):
        if IS_WIN:
            jp, disp, mono = "Yu Gothic UI", "Bahnschrift", "Consolas"
        else:
            jp, disp, mono = "Noto Sans CJK JP", "DejaVu Sans", "DejaVu Sans Mono"
        self.F = {
            "base": (jp, 10), "small": (jp, 9), "bold": (jp, 10, "bold"),
            "disp": (disp, 10, "bold"), "disp_s": (disp, 8, "bold"),
            "title": (disp, 20, "bold"), "sub": (disp, 9),
            "mono": (mono, 10), "mono_s": (mono, 9),
        }
        self.option_add("*Font", self.F["base"])

    def _style(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=C["bg"], foreground=C["text"], fieldbackground=C["panel2"],
                     bordercolor=C["line"], lightcolor=C["panel"], darkcolor=C["panel"],
                     troughcolor=C["bg"], focuscolor=C["cyan"], selectbackground=C["sel"],
                     selectforeground=C["cyan"], insertcolor=C["cyan"], font=self.F["base"])
        st.configure("Neo.Treeview", background=C["panel"], fieldbackground=C["panel"],
                     foreground=C["text"], rowheight=28, borderwidth=0, relief="flat")
        st.map("Neo.Treeview", background=[("selected", C["sel"])], foreground=[("selected", C["cyan"])])
        st.layout("Neo.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        st.configure("Neo.Treeview.Heading", background=C["panel2"], foreground=C["cyan"],
                     relief="flat", borderwidth=0, font=self.F["disp_s"], padding=(6, 6))
        st.map("Neo.Treeview.Heading", background=[("active", C["line"])])
        st.configure("Neo.TCombobox", fieldbackground=C["panel2"], background=C["panel2"],
                     foreground=C["text"], arrowcolor=C["cyan"], bordercolor=C["line"],
                     lightcolor=C["panel2"], darkcolor=C["panel2"], padding=4)
        st.map("Neo.TCombobox", fieldbackground=[("readonly", C["panel2"])],
               foreground=[("readonly", C["text"])], bordercolor=[("focus", C["cyan"])],
               selectbackground=[("readonly", C["panel2"])], selectforeground=[("readonly", C["cyan"])])
        self.option_add("*TCombobox*Listbox.background", C["panel2"])
        self.option_add("*TCombobox*Listbox.foreground", C["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", C["sel"])
        self.option_add("*TCombobox*Listbox.selectForeground", C["cyan"])
        st.configure("Neo.Vertical.TScrollbar", background=C["panel2"], troughcolor=C["panel"],
                     bordercolor=C["panel"], arrowcolor=C["cyan"], lightcolor=C["panel2"],
                     darkcolor=C["panel2"], gripcount=0, arrowsize=12)
        st.map("Neo.Vertical.TScrollbar", background=[("active", C["cyan_dim"])])

    # ---- 部品 -------------------------------------------------------------
    def _panel(self, parent, en, ja):
        """細い発光枠 + HUD 見出し付きパネル。 (外枠, 中身) を返す。"""
        outer = tk.Frame(parent, bg=C["line"])
        inner = tk.Frame(outer, bg=C["panel"])
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        hdr = tk.Canvas(inner, height=28, bg=C["panel"], highlightthickness=0)
        hdr.pack(fill="x")

        def draw(_e=None):
            hdr.delete("all")
            w = max(hdr.winfo_width(), 120)
            hdr.create_rectangle(0, 6, 3, 22, fill=C["cyan"], width=0)
            t = hdr.create_text(12, 14, anchor="w", text=en, fill=C["cyan"], font=self.F["disp"])
            x = hdr.bbox(t)[2] + 8
            t2 = hdr.create_text(x, 14, anchor="w", text=ja, fill=C["dim"], font=self.F["small"])
            x2 = hdr.bbox(t2)[2] + 10
            if x2 < w - 40:
                hdr.create_line(x2, 14, w - 36, 14, fill=C["line"])
            hdr.create_polygon(w - 30, 10, w - 10, 10, w - 14, 18, w - 34, 18, fill=C["cyan_dim"], outline="")
            hdr.create_line(0, 27, w, 27, fill=C["line"])
        hdr.bind("<Configure>", draw)
        body = tk.Frame(inner, bg=C["panel"])
        body.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        return outer, body

    def _entry(self, parent, var):
        return tk.Entry(parent, textvariable=var, bg=C["panel2"], fg=C["text"], relief="flat",
                        insertbackground=C["cyan"], highlightthickness=1,
                        highlightbackground=C["line"], highlightcolor=C["cyan"],
                        font=self.F["mono"], selectbackground=C["sel"], selectforeground=C["cyan"])

    def _spin(self, parent, var, frm, to, inc, width):
        return tk.Spinbox(parent, from_=frm, to=to, increment=inc, width=width, textvariable=var,
                          bg=C["panel2"], fg=C["cyan"], relief="flat", buttonbackground=C["panel2"],
                          insertbackground=C["cyan"], highlightthickness=1,
                          highlightbackground=C["line"], highlightcolor=C["cyan"], font=self.F["mono"])

    def _check(self, parent, text, var):
        return tk.Checkbutton(parent, text=text, variable=var, bg=C["bg"], fg=C["text"],
                              activebackground=C["bg"], activeforeground=C["cyan"],
                              selectcolor=C["panel2"], highlightthickness=0, bd=0, font=self.F["small"])

    # ---- 画面構築 ---------------------------------------------------------
    def _build(self):
        # ── ヘッダー (HUD) ──
        self.hdr = tk.Canvas(self, height=72, bg=C["bg"], highlightthickness=0)
        self.hdr.pack(fill="x")
        self.hdr.bind("<Configure>", lambda e: self._draw_header())

        # ── ツールバー ──
        tb = tk.Frame(self, bg=C["bg"], padx=10, pady=6)
        tb.pack(fill="x")
        for txt, cmd in (("＋ 新規", self.new), ("⇪ 開く", self.open), ("⬇ 保存", self.save)):
            NeonButton(tb, txt, cmd).pack(side="left", padx=3)
        tk.Frame(tb, bg=C["line"], width=1, height=26).pack(side="left", padx=10)
        self.btn_run = NeonButton(tb, "▶  RUN  実行 (F5)", self.run_macro, filled=True, font=self.F["disp"], padx=18)
        self.btn_run.pack(side="left", padx=3)
        self.btn_stop = NeonButton(tb, "■  STOP  停止", self.stop_macro, color=C["mag"], font=self.F["disp"], padx=14)
        self.btn_stop.pack(side="left", padx=3)
        self.btn_stop.set_enabled(False)
        tk.Frame(tb, bg=C["line"], width=1, height=26).pack(side="left", padx=10)
        self._check(tb, "ドライラン(キーを送らない)", self.var_dry).pack(side="left", padx=4)
        self._check(tb, "実行中は最小化", self.var_minimize).pack(side="left", padx=4)
        right = tk.Frame(tb, bg=C["bg"])
        right.pack(side="right")
        for lbl, var, a, b, inc, w in (("開始待ち(秒)", self.var_delay, 0, 60, 0.5, 4),
                                       ("キー間隔(ms)", self.var_interval, 0, 2000, 10, 5)):
            tk.Label(right, text=lbl, bg=C["bg"], fg=C["dim"], font=self.F["small"]).pack(side="left", padx=(10, 4))
            self._spin(right, var, a, b, inc, w).pack(side="left")

        # ── ステータスバー / ログ (下から先に配置) ──
        sb = tk.Frame(self, bg=C["panel"], height=26)
        sb.pack(fill="x", side="bottom")
        tk.Label(sb, text=" SYS ", bg=C["cyan"], fg=C["bg"], font=self.F["disp_s"]).pack(side="left", fill="y")
        self.st = {}
        for key in ("ver", "file", "count", "stop", "mode", "msg"):
            if key != "ver":
                tk.Label(sb, text="│", bg=C["panel"], fg=C["line2"]).pack(side="left")
            lb = tk.Label(sb, text="", bg=C["panel"], fg=C["dim"], font=self.F["mono_s"], padx=8)
            lb.pack(side="left")
            self.st[key] = lb
        self.st["ver"].configure(text=f"{APP_NAME.upper()} v{APP_VERSION}", fg=C["cyan"])
        self.st["stop"].configure(text="EMERGENCY STOP: F12", fg=C["mag"])
        self.st["mode"].configure(text="WINDOWS" if IS_WIN else "SIMULATION (非Windows)")

        log_outer, log_body = self._panel(self, "TERMINAL", "実行ログ")
        log_outer.pack(fill="x", side="bottom", padx=10, pady=(0, 8))
        self.txt_log = tk.Text(log_body, height=7, state="disabled", bg=C["term"], fg=C["text"],
                               insertbackground=C["cyan"], relief="flat", bd=0, padx=8, pady=6,
                               font=self.F["mono_s"], highlightthickness=0)
        self.txt_log.pack(fill="x")
        for lv, col in (("info", C["text"]), ("head", C["cyan"]), ("warn", C["warn"]),
                        ("error", C["err"]), ("user", C["ok"]), ("time", C["dim"])):
            self.txt_log.tag_configure(lv, foreground=col)

        # ── 本体 (3 カラム) ──
        body = tk.PanedWindow(self, orient="horizontal", bg=C["bg"], sashwidth=8, bd=0, sashrelief="flat")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        # 左: コマンドパレット
        pal_outer, pal = self._panel(body, "MODULES", "コマンド追加")
        body.add(pal_outer, minsize=300, width=300)
        for group_en, group, types in PALETTE_UI:
            tk.Label(pal, text=f"// {group_en}  {group}", bg=C["panel"], fg=C["dim"],
                     font=self.F["disp_s"]).pack(anchor="w", pady=(8, 3))
            g = tk.Frame(pal, bg=C["panel"])
            g.pack(anchor="w")
            for n, t in enumerate(types):
                NeonButton(g, CMD_DEFS[t]["label"], lambda t=t: self.add_cmd(t),
                           color=CAT_COLOR[CMD_DEFS[t]["cat"]], width=13, anchor="w",
                           font=self.F["small"], padx=6, pady=3).grid(row=n // 2, column=n % 2, padx=2, pady=2, sticky="w")

        # 中央: シーケンス
        mid_outer, mid = self._panel(body, "SEQUENCE", "マクロ編集")
        body.add(mid_outer, minsize=500, stretch="always")
        tabbar = tk.Frame(mid, bg=C["panel"])
        tabbar.pack(fill="x", pady=(0, 6))
        self.tab_btns = []
        for i, (en, ja) in enumerate((("BLOCKS", "ブロック編集"), ("SCRIPT", "スクリプト"))):
            b = tk.Label(tabbar, text=f"{en}  {ja}", bg=C["panel"], fg=C["dim"], font=self.F["disp"],
                         padx=14, pady=5, cursor="hand2")
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, i=i: self.select_tab(i))
            b.bind("<Configure>", lambda e: self._draw_tab_line())
            self.tab_btns.append(b)
        self.lbl_check = tk.Label(tabbar, text="", bg=C["panel"], font=self.F["small"])
        self.lbl_check.pack(side="right", padx=4)
        self.tab_line = tk.Canvas(mid, height=2, bg=C["line"], highlightthickness=0)
        self.tab_line.pack(fill="x")
        self.tab_line.bind("<Configure>", lambda e: self._draw_tab_line())

        self.page_tree = tk.Frame(mid, bg=C["panel"])
        tf = tk.Frame(self.page_tree, bg=C["line"])
        tf.pack(fill="both", expand=True, pady=(6, 6))
        self.tree = ttk.Treeview(tf, columns=("no", "cmd", "detail"), show="headings",
                                 selectmode="browse", style="Neo.Treeview")
        for col, txt, w, st in (("no", "#", 48, False), ("cmd", "COMMAND  コマンド", 240, False),
                                ("detail", "PARAMETERS  内容", 360, True)):
            self.tree.heading(col, text=txt, anchor="w" if col != "no" else "e")
            self.tree.column(col, width=w, stretch=st, anchor="e" if col == "no" else "w")
        for cat, col in CAT_COLOR.items():
            self.tree.tag_configure(cat, foreground=col)
        self.tree.tag_configure("odd", background=C["panel_alt"])
        self.tree.tag_configure("running", background=C["run_bg"], foreground=C["mag"])
        ysb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview, style="Neo.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1)
        ysb.pack(side="left", fill="y", pady=1, padx=(0, 1))
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_props())
        self.tree.bind("<Delete>", lambda e: self.delete_cmd())
        ops = tk.Frame(self.page_tree, bg=C["panel"])
        ops.pack(fill="x")
        for txt, cmd in (("▲ 上へ", lambda: self.move(-1)), ("▼ 下へ", lambda: self.move(1)),
                         ("⧉ 複製", self.dup_cmd)):
            NeonButton(ops, txt, cmd, font=self.F["small"], padx=10, pady=3).pack(side="left", padx=2)
        NeonButton(ops, "✕ 削除", self.delete_cmd, color=C["err"], font=self.F["small"],
                   padx=10, pady=3).pack(side="left", padx=2)

        self.page_script = tk.Frame(mid, bg=C["panel"])
        sf = tk.Frame(self.page_script, bg=C["line"])
        sf.pack(fill="both", expand=True, pady=(6, 6))
        self.txt_script = tk.Text(sf, wrap="none", undo=True, bg=C["term"], fg=C["ok"],
                                  insertbackground=C["cyan"], relief="flat", bd=0, padx=10, pady=8, highlightthickness=0,
                                  font=self.F["mono"], selectbackground=C["sel"], selectforeground=C["cyan"])
        self.txt_script.pack(fill="both", expand=True, padx=1, pady=1)
        sbar = tk.Frame(self.page_script, bg=C["panel"])
        sbar.pack(fill="x")
        NeonButton(sbar, "⇦ ブロックに反映", self.apply_script, font=self.F["small"], padx=10, pady=3).pack(side="left", padx=2)
        tk.Label(sbar, text="FORMAT: COMMAND key=value …   例) KEY ENTER 3 / TYPE text='Hi ${i}'",
                 bg=C["panel"], fg=C["dim"], font=self.F["mono_s"]).pack(side="left", padx=10)
        self.select_tab(0, init=True)

        # 右: プロパティ
        prop_outer, right = self._panel(body, "INSPECTOR", "プロパティ")
        body.add(prop_outer, minsize=280, width=290)
        self.prop_frame = tk.Frame(right, bg=C["panel"])
        self.prop_frame.pack(fill="x", pady=2)
        tk.Frame(right, bg=C["line"], height=1).pack(fill="x", pady=10)
        tk.Label(right, text="// FUNCTIONS  式で使える関数", bg=C["panel"], fg=C["dim"],
                 font=self.F["disp_s"]).pack(anchor="w")
        tk.Label(right, text=FUNC_TEXT, bg=C["panel"], fg=C["cyan_mid"], justify="left",
                 font=self.F["mono_s"]).pack(anchor="w", pady=4)
        tk.Label(right, text="// OPERATORS  演算子", bg=C["panel"], fg=C["dim"],
                 font=self.F["disp_s"]).pack(anchor="w", pady=(6, 0))
        tk.Label(right, text="+ - * / // % **\n== != < > <= >=\nand or not   埋込 ${式}",
                 bg=C["panel"], fg=C["cyan_mid"], justify="left", font=self.F["mono_s"]).pack(anchor="w", pady=4)

    # ---- ヘッダー描画 / アニメーション -------------------------------------
    def _draw_header(self):
        h = self.hdr
        h.delete("all")
        w, H = max(h.winfo_width(), 400), 72
        for x in range(0, w, 28):                       # グリッド
            h.create_line(x, 0, x, H, fill=C["grid"])
        for y in range(0, H, 14):
            h.create_line(0, y, w, y, fill=C["grid"])
        h.create_polygon(14, 18, 30, 18, 22, 54, 6, 54, fill=C["cyan"], outline="")   # ロゴ記号
        h.create_polygon(34, 18, 44, 18, 36, 54, 26, 54, fill=C["mag"], outline="")
        for dx, dy, col in ((2, 2, C["cyan_dim"]), (0, 0, C["cyan"])):               # 発光風タイトル
            h.create_text(56 + dx, 30 + dy, anchor="w", text="KEY MACRO STUDIO", fill=col, font=self.F["title"])
        h.create_text(58, 54, anchor="w", fill=C["dim"], font=self.F["sub"],
                      text=f"AUTOMATION SEQUENCER  //  BUILD v{APP_VERSION}  //  キー操作自動化システム")
        # 右側: 状態ピル + 時計
        h.create_rectangle(w - 330, 18, w - 170, 52, outline=C["line2"], fill=C["panel"], tags="pill_box")
        h.create_oval(w - 318, 29, w - 306, 41, fill=C["cyan"], outline="", tags="dot")
        h.create_text(w - 296, 35, anchor="w", text="STANDBY", fill=C["cyan"], font=self.F["disp"], tags="state")
        h.create_text(w - 20, 28, anchor="e", text="", fill=C["text"], font=(self.F["mono"][0], 14, "bold"), tags="clock")
        h.create_text(w - 20, 48, anchor="e", text="", fill=C["dim"], font=self.F["mono_s"], tags="date")
        h.create_line(0, H - 1, w, H - 1, fill=C["line"])
        h.create_rectangle(0, H - 3, 140, H - 1, fill=C["cyan"], width=0, tags="scan")
        self._hdr_w = w
        self._update_header_state()

    def _update_header_state(self):
        col = {"STANDBY": C["cyan"], "RUNNING": C["mag"], "DRY RUN": C["warn"]}[self.run_state]
        self.hdr.itemconfigure("state", text=self.run_state, fill=col)
        self.hdr.itemconfigure("dot", fill=col)
        self.hdr.itemconfigure("scan", fill=col)
        self.hdr.itemconfigure("pill_box", outline=col if self.run_state != "STANDBY" else C["line2"])

    def _animate(self):
        w = getattr(self, "_hdr_w", 800)
        speed = 14 if self.run_state != "STANDBY" else 5
        self._scan_x = (self._scan_x + speed) % (w + 140)
        self.hdr.coords("scan", self._scan_x - 140, 69, self._scan_x, 71)
        self._pulse = (self._pulse + 1) % 20
        if self.run_state != "STANDBY":
            on = self._pulse < 10
            base = C["mag"] if self.run_state == "RUNNING" else C["warn"]
            self.hdr.itemconfigure("dot", fill=base if on else C["panel"])
        self.hdr.itemconfigure("clock", text=time.strftime("%H:%M:%S"))
        self.hdr.itemconfigure("date", text=time.strftime("%Y.%m.%d  %a").upper())
        if not self._closing:
            self.after(40, self._animate)

    def set_run_state(self, state):
        self.run_state = state
        self._update_header_state()

    # ---- タブ -------------------------------------------------------------
    def select_tab(self, i, init=False):
        if not init and i == self.tab_index:
            return
        if not init and self.tab_index == 1 and self.txt_script.edit_modified():
            if not self.apply_script(silent=True):
                return
        self.tab_index = i
        for n, b in enumerate(self.tab_btns):
            b.configure(fg=C["cyan"] if n == i else C["dim"], bg=C["panel2"] if n == i else C["panel"])
        self.page_tree.pack_forget()
        self.page_script.pack_forget()
        if i == 0:
            self.page_tree.pack(fill="both", expand=True)
        else:
            self.txt_script.delete("1.0", "end")
            self.txt_script.insert("1.0", to_script(self.cmds))
            self.txt_script.edit_modified(False)
            self.page_script.pack(fill="both", expand=True)
        self.after(10, self._draw_tab_line)

    def _draw_tab_line(self):
        c = self.tab_line
        c.delete("all")
        b = self.tab_btns[self.tab_index]
        x0 = b.winfo_x()
        c.create_rectangle(x0, 0, x0 + b.winfo_width(), 2, fill=C["cyan"], width=0)

    # ---- ツリー / プロパティ ---------------------------------------------
    def sel_index(self):
        s = self.tree.selection()
        return int(s[0]) if s else None

    def refresh_tree(self, select=None):
        self.tree.delete(*self.tree.get_children())
        for i, (c, d) in enumerate(zip(self.cmds, calc_depths(self.cmds))):
            df = CMD_DEFS[c["type"]]
            ind = "  │ " * d
            if c["type"] in BLOCK_START:
                label = f"{ind}◢ {df['label']}"
            elif c["type"] in ("END", "ELSE"):
                label = f"{ind}◥ {df['label']}"
            else:
                label = f"{ind}  {df['label']}"
            tags = (df["cat"],) + (("odd",) if i % 2 else ())
            self.tree.insert("", "end", iid=str(i), values=(f"{i + 1:02d}", label, cmd_summary(c)), tags=tags)
        if select is not None and 0 <= select < len(self.cmds):
            self.tree.selection_set(str(select))
            self.tree.see(str(select))
        self._update_status()

    def _update_status(self):
        try:
            build_blocks(self.cmds)
            self.lbl_check.configure(text="◆ SYNTAX OK  構文OK", fg=C["ok"])
        except MacroError as e:
            self.lbl_check.configure(text=f"▲ {e}", fg=C["err"])
        name = os.path.basename(self.filepath) if self.filepath else "(未保存)"
        self.st["file"].configure(text=f"FILE: {name}")
        self.st["count"].configure(text=f"STEPS: {len(self.cmds)}")

    def show_props(self):
        for w in self.prop_frame.winfo_children():
            w.destroy()
        self.prop_widgets = {}
        i = self.sel_index()
        if i is None:
            tk.Label(self.prop_frame, text="コマンドを選択してください", bg=C["panel"],
                     fg=C["dim"]).pack(anchor="w", pady=6)
            return
        c = self.cmds[i]
        df = CMD_DEFS[c["type"]]
        col = CAT_COLOR[df["cat"]]
        top = tk.Frame(self.prop_frame, bg=C["panel"])
        top.pack(fill="x", pady=(0, 8))
        tk.Label(top, text=f"{i + 1:02d}", bg=col, fg=C["bg"], font=self.F["disp"], padx=6).pack(side="left")
        tk.Label(top, text=f" {df['label']}", bg=C["panel"], fg=col, font=self.F["bold"]).pack(side="left")
        tk.Label(top, text=c["type"], bg=C["panel"], fg=C["dim"], font=self.F["mono_s"]).pack(side="right")
        if not df["fields"]:
            tk.Label(self.prop_frame, text="設定項目はありません", bg=C["panel"], fg=C["dim"]).pack(anchor="w")
        for key, lbl, _d, hint in df["fields"]:
            tk.Label(self.prop_frame, text=f"› {lbl}", bg=C["panel"], fg=C["text"],
                     font=self.F["small"]).pack(anchor="w", pady=(4, 1))
            var = tk.StringVar(value=str(c.get(key, "")))
            if key == "button":
                w = ttk.Combobox(self.prop_frame, textvariable=var, values=["left", "right", "middle"],
                                 state="readonly", style="Neo.TCombobox")
            elif key == "key":
                w = ttk.Combobox(self.prop_frame, textvariable=var, values=sorted(VK.keys()), style="Neo.TCombobox")
            else:
                w = self._entry(self.prop_frame, var)
            w.pack(fill="x", ipady=3)
            var.trace_add("write", lambda *_a, k=key, v=var: self._prop_changed(k, v))
            if hint:
                tk.Label(self.prop_frame, text=hint, bg=C["panel"], fg=C["dim"], wraplength=250,
                         justify="left", font=self.F["small"]).pack(anchor="w", pady=(1, 2))
            self.prop_widgets[key] = var
        keys = [k for k, *_ in df["fields"]]
        if "title" in keys:
            NeonButton(self.prop_frame, "◎ 開いているウィンドウから選ぶ", self.pick_window,
                       font=self.F["small"], pady=4).pack(fill="x", pady=(8, 2))
        if "x" in keys:
            NeonButton(self.prop_frame, "⌖ マウス位置を取得 (3秒後)", self.capture_pos,
                       font=self.F["small"], pady=4).pack(fill="x", pady=(8, 2))

    def _prop_changed(self, key, var):
        i = self.sel_index()
        if i is None:
            return
        self.cmds[i][key] = var.get()
        self.tree.set(str(i), "detail", cmd_summary(self.cmds[i]))
        self._update_status()

    # ---- 編集操作 ---------------------------------------------------------
    def add_cmd(self, ctype):
        i = self.sel_index()
        pos = len(self.cmds) if i is None else i + 1
        self.cmds.insert(pos, new_cmd(ctype))
        if ctype in BLOCK_START:
            self.cmds.insert(pos + 1, new_cmd("END"))
        self.refresh_tree(select=pos)
        self.show_props()

    def delete_cmd(self):
        i = self.sel_index()
        if i is None:
            return
        del self.cmds[i]
        self.refresh_tree(select=min(i, len(self.cmds) - 1))
        self.show_props()

    def dup_cmd(self):
        i = self.sel_index()
        if i is None:
            return
        self.cmds.insert(i + 1, dict(self.cmds[i]))
        self.refresh_tree(select=i + 1)

    def move(self, d):
        i = self.sel_index()
        if i is None or not (0 <= i + d < len(self.cmds)):
            return
        self.cmds[i], self.cmds[i + d] = self.cmds[i + d], self.cmds[i]
        self.refresh_tree(select=i + d)

    def pick_window(self):
        wins = [t for _h, t in WIN.list_windows()]
        if not wins:
            messagebox.showinfo(APP_NAME, "ウィンドウ一覧を取得できません(Windows でのみ利用可)")
            return
        dlg = tk.Toplevel(self, bg=C["panel"])
        dlg.title("ウィンドウを選択")
        dlg.geometry("480x380")
        tk.Label(dlg, text="TARGET WINDOW  対象ウィンドウ", bg=C["panel"], fg=C["cyan"],
                 font=self.F["disp"]).pack(anchor="w", padx=10, pady=(10, 4))
        lb = tk.Listbox(dlg, bg=C["panel2"], fg=C["text"], selectbackground=C["sel"],
                        selectforeground=C["cyan"], relief="flat", highlightthickness=1,
                        highlightbackground=C["line"], highlightcolor=C["cyan"], font=self.F["base"])
        lb.pack(fill="both", expand=True, padx=10, pady=4)
        for t in wins:
            lb.insert("end", t)

        def ok(_e=None):
            s = lb.curselection()
            if s and "title" in self.prop_widgets:
                self.prop_widgets["title"].set(wins[s[0]])
            dlg.destroy()
        lb.bind("<Double-1>", ok)
        NeonButton(dlg, "選択", ok, filled=True).pack(pady=8)

    def capture_pos(self):
        self.st["msg"].configure(text="3秒後にマウス位置を取得… カーソルを目的の位置へ", fg=C["warn"])

        def grab():
            x, y = WIN.cursor_pos()
            if "x" in self.prop_widgets:
                self.prop_widgets["x"].set(str(x))
                self.prop_widgets["y"].set(str(y))
            self.st["msg"].configure(text="")
            self.log_line(f"マウス位置: ({x}, {y})", "head")
        self.after(3000, grab)

    # ---- スクリプト反映 ---------------------------------------------------
    def apply_script(self, silent=False):
        try:
            cmds = from_script(self.txt_script.get("1.0", "end"))
        except ValueError as e:
            messagebox.showerror(APP_NAME, f"スクリプトの解析に失敗しました\n\n{e}")
            return False
        self.cmds = cmds
        self.txt_script.edit_modified(False)
        self.refresh_tree()
        self.show_props()
        if not silent:
            self.log_line(f"スクリプトを反映しました ({len(cmds)} コマンド)", "head")
        return True

    # ---- ファイル ---------------------------------------------------------
    def new(self):
        if messagebox.askyesno(APP_NAME, "現在の内容を破棄して新規作成しますか?"):
            self.cmds, self.filepath = [], None
            self.refresh_tree()
            self.show_props()

    def open(self):
        p = filedialog.askopenfilename(initialdir=self._macro_dir(),
                                       filetypes=[("Key Macro", f"*{FILE_EXT}"), ("JSON", "*.json"),
                                                  ("スクリプト", "*.txt"), ("すべて", "*.*")])
        if p:
            self.load_file(p)

    def load_file(self, p):
        try:
            with open(p, encoding="utf-8") as f:
                raw = f.read()
            if p.endswith(".txt"):
                self.cmds = from_script(raw)
            else:
                data = json.loads(raw)
                self.cmds = [c for c in data["commands"] if c.get("type") in CMD_DEFS]
                s = data.get("settings", {})
                self.var_delay.set(s.get("start_delay", 3))
                self.var_interval.set(s.get("key_interval_ms", 50))
            self.filepath = p
            self.refresh_tree(select=0)
            self.show_props()
            if self.tab_index == 1:
                self.select_tab(1, init=True)
            self.log_line(f"読込: {p}", "head")
        except Exception as e:
            messagebox.showerror(APP_NAME, f"読込に失敗しました\n{e}")

    def save(self):
        if self.tab_index == 1 and self.txt_script.edit_modified():
            if not self.apply_script(silent=True):
                return
        p = self.filepath
        if not p or p.endswith(".txt"):
            p = filedialog.asksaveasfilename(defaultextension=FILE_EXT, initialdir=self._macro_dir(),
                                             initialfile=f"macro{FILE_EXT}",
                                             filetypes=[("Key Macro", f"*{FILE_EXT}"), ("スクリプト", "*.txt")])
            if not p:
                return
        try:
            with open(p, "w", encoding="utf-8") as f:
                if p.endswith(".txt"):
                    f.write(to_script(self.cmds))
                else:
                    json.dump({"app": APP_NAME, "version": APP_VERSION,
                               "settings": self._settings(), "commands": self.cmds},
                              f, ensure_ascii=False, indent=2)
            self.filepath = p
            self._update_status()
            self.log_line(f"保存: {p}", "head")
        except Exception as e:
            messagebox.showerror(APP_NAME, f"保存に失敗しました\n{e}")

    def _macro_dir(self):
        try:
            os.makedirs(MACRO_DIR, exist_ok=True)
            return MACRO_DIR
        except OSError:
            return DATA_DIR

    # ---- 設定の保存/復元 (exe と同じフォルダ) -----------------------------
    def _load_prefs(self):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                p = json.load(f)
        except (OSError, ValueError):
            return {}
        try:
            self.var_delay.set(float(p.get("start_delay", 3.0)))
            self.var_interval.set(int(p.get("key_interval_ms", 50)))
            self.var_dry.set(bool(p.get("dry_run", not IS_WIN)) or not IS_WIN)
            self.var_minimize.set(bool(p.get("minimize", True)))
            if re.fullmatch(r"\d+x\d+[+-]-?\d+[+-]-?\d+", str(p.get("geometry", ""))):
                self.geometry(p["geometry"])
        except (tk.TclError, ValueError, TypeError):
            pass
        return p

    def _save_prefs(self, keep_geometry=False):
        try:
            data = {"version": APP_VERSION, "start_delay": float(self.var_delay.get()),
                    "key_interval_ms": int(self.var_interval.get()), "dry_run": bool(self.var_dry.get()),
                    "minimize": bool(self.var_minimize.get()),
                    "geometry": self._prefs.get("geometry", "") if keep_geometry else self.geometry(),
                    "last_file": self.filepath or ""}
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except (OSError, tk.TclError, ValueError):
            pass

    def _on_tk_error(self, exc, val, tb):
        import traceback
        text = "".join(traceback.format_exception(exc, val, tb))
        write_error_log(text)
        try:
            self.log_line(f"予期しないエラー: {val}  (詳細: {os.path.basename(ERROR_LOG)})", "error")
        except Exception:
            pass

    def _settings(self):
        return {"start_delay": float(self.var_delay.get()), "key_interval_ms": int(self.var_interval.get())}

    # ---- 実行 -------------------------------------------------------------
    def run_macro(self):
        if self.runner and self.runner.is_alive():
            return
        if self.tab_index == 1 and self.txt_script.edit_modified():
            if not self.apply_script(silent=True):
                return
        try:
            build_blocks(self.cmds)
        except MacroError as e:
            messagebox.showerror(APP_NAME, f"構文エラーがあります\n\n{e}")
            return
        self.runner = MacroRunner(self.cmds, self._settings(), self.q, dry_run=self.var_dry.get())
        self.btn_run.set_enabled(False)
        self.btn_stop.set_enabled(True)
        self.set_run_state("DRY RUN" if self.runner.dry else "RUNNING")
        if self.var_minimize.get() and not self.runner.dry:
            self.iconify()
        self.runner.start()

    def stop_macro(self):
        if self.runner:
            self.runner.stop()

    def _clear_running(self):
        for iid in self.tree.tag_has("running"):
            tags = [t for t in self.tree.item(iid, "tags") if t != "running"]
            self.tree.item(iid, tags=tags)

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "log":
                    self.log_line(msg[2], msg[1])
                elif msg[0] == "pc":
                    self._clear_running()
                    iid = str(msg[1])
                    if self.tree.exists(iid):
                        self.tree.item(iid, tags=tuple(self.tree.item(iid, "tags")) + ("running",))
                        self.tree.see(iid)
                elif msg[0] == "done":
                    self.btn_run.set_enabled(True)
                    self.btn_stop.set_enabled(False)
                    self._clear_running()
                    self.set_run_state("STANDBY")
                    if self.state() == "iconic":
                        self.deiconify()
        except queue.Empty:
            pass
        if not self._closing:
            self.after(60, self._poll)

    def log_line(self, text, level="info"):
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", time.strftime("[%H:%M:%S] "), "time")
        self.txt_log.insert("end", "› " + text + "\n", level)
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _on_close(self):
        if self.runner and self.runner.is_alive():
            self.runner.stop()
        self._save_prefs(keep_geometry=self.state() != "normal")
        self._closing = True
        self.after(100, self.destroy)   # 周期処理が止まってから破棄


def main():
    try:
        App().mainloop()
    except Exception:
        import traceback
        write_error_log(traceback.format_exc())
        try:
            messagebox.showerror(APP_NAME, f"起動中にエラーが発生しました。\n{ERROR_LOG} を確認してください。")
        except Exception:
            pass


if __name__ == "__main__":
    main()
