"""
uia_sender.py — 基于 Windows UI Automation 的微信 4.0+ 消息发送器
源于 Akasha-WeChat 项目的成熟实现。

原理：
  微信 4.0 基于 Electron (Chromium)。Chromium 通过 UIA 桥将 HTML 输入元素
  暴露为标准 UIA 控件。通过 ValuePattern 设置输入框文本，InvokePattern 点击
  发送按钮。全程无鼠标键盘模拟（降级模式下有），无 DLL 注入。

工作流：
  1. 定位微信窗口 (Electron/Chromium 或 Qt)
  2. Ctrl+F 搜索联系人 → Enter → 切换到目标聊天
  3. 定位聊天输入框 (EditControl + ValuePattern)
  4. 设置文本 → 点击发送按钮或 Enter
"""
import logging
import os
import subprocess
import threading
import time

log = logging.getLogger(__name__)


class UiaSender:
    """Windows UI Automation 微信发送器。"""

    WECHAT_TITLES = ["微信", "WeChat"]
    EXCLUDE_CLASSES = ["Chrome_WidgetWin_1", "CabinetWClass"]

    def __init__(self, search_enabled: bool = True):
        self._lock = threading.Lock()
        self._auto = None
        self._ready = False
        self._window = None
        self._is_electron = False
        self._input_control = None
        self._send_button = None
        self._last_contact = ""
        self._use_coord_fallback = False
        self.search_enabled = search_enabled
        self._init()

    def _init(self):
        try:
            import uiautomation as auto
            self._auto = auto
        except ImportError:
            log.error("请先安装 uiautomation: pip install uiautomation")
            return
        log.info("正在搜索微信窗口...")
        self._find_window()
        if self._window:
            log.info("微信窗口: '%s' ClassName=%s", self._window.Name, self._window.ClassName)
            self._ready = True

    def _find_window(self):
        auto = self._auto
        root = auto.GetRootControl()
        for w in root.GetChildren():
            cls = w.ClassName
            if cls in self.EXCLUDE_CLASSES:
                continue
            for kw in self.WECHAT_TITLES:
                if kw in w.Name:
                    self._window = w
                    if cls != "WeChatMainWndForPC":
                        self._is_electron = True
                    return

    def _ensure_window(self) -> bool:
        if not self._ready:
            return False
        if self._window and self._window.Exists(0.2):
            return True
        self._find_window()
        if not self._window:
            log.warning("微信窗口未找到")
            self._ready = False
            return False
        return True

    def _activate(self):
        try:
            import ctypes
            hwnd = ctypes.windll.user32.FindWindowW('Qt51514QWindowIcon', None)
            if not hwnd:
                hwnd = ctypes.windll.user32.FindWindowW('WeChatMainWndForPC', None)
            if hwnd:
                WE_CHAT_TID = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, None)
                CURRENT_TID = ctypes.windll.kernel32.GetCurrentThreadId()
                ctypes.windll.user32.AttachThreadInput(CURRENT_TID, WE_CHAT_TID, True)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                ctypes.windll.user32.BringWindowToTop(hwnd)
                ctypes.windll.user32.AttachThreadInput(CURRENT_TID, WE_CHAT_TID, False)
        except Exception:
            pass

    def _switch_contact(self, contact: str) -> bool:
        """Ctrl+F → 粘贴联系人名 → Enter"""
        if not self._ensure_window():
            return False
        self._activate()

        try:
            import ctypes
            hwnd = ctypes.windll.user32.FindWindowW('Qt51514QWindowIcon', None)
            if not hwnd:
                hwnd = ctypes.windll.user32.FindWindowW('WeChatMainWndForPC', None)
            if not hwnd:
                return False

            WE_CHAT_TID = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, None)
            CURRENT_TID = ctypes.windll.kernel32.GetCurrentThreadId()
            ctypes.windll.user32.AttachThreadInput(CURRENT_TID, WE_CHAT_TID, True)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            time.sleep(0.3)

            # Ctrl+F
            ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x46, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x46, 0, 2, 0)
            ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
            time.sleep(0.5)

            # Ctrl+A 清空 + Ctrl+V 粘贴
            ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x41, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x41, 0, 2, 0)
            ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
            time.sleep(0.15)

            import pyperclip
            pyperclip.copy(contact)
            time.sleep(0.1)
            ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x56, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x56, 0, 2, 0)
            ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
            time.sleep(0.3)

            # Enter
            ctypes.windll.user32.keybd_event(0x0D, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x0D, 0, 2, 0)
            time.sleep(0.8)

            return True
        finally:
            try:
                ctypes.windll.user32.AttachThreadInput(CURRENT_TID, WE_CHAT_TID, False)
            except Exception:
                pass

    def _locate_input(self) -> bool:
        if not self._ensure_window():
            return False
        if self._input_control is not None:
            try:
                self._input_control.GetCurrentPattern()
                return True
            except Exception:
                self._input_control = None
                self._send_button = None

        auto = self._auto
        win_rect = self._window.BoundingRectangle
        win_center_y = win_rect.top + win_rect.height() / 2

        edits = []

        def walk(ctrl, depth=0):
            if depth > 14:
                return
            try:
                for child in ctrl.GetChildren():
                    try:
                        if child.ControlTypeName == "EditControl":
                            edits.append(child)
                        walk(child, depth + 1)
                    except Exception:
                        pass
            except Exception:
                pass

        try:
            walk(self._window)
        except Exception:
            pass

        if not edits:
            log.warning("未找到输入控件，使用坐标后备方案")
            self._use_coord_fallback = True
            return True

        candidates = [e for e in edits
                      if e.BoundingRectangle and
                      e.BoundingRectangle.top >= win_center_y - 20 and
                      e.BoundingRectangle.width() > 100]

        if not candidates:
            candidates = [e for e in edits if e.BoundingRectangle]

        candidates.sort(key=lambda e: e.BoundingRectangle.width() *
                        e.BoundingRectangle.height(), reverse=True)

        for ctrl in candidates:
            rect = ctrl.BoundingRectangle
            if rect.width() * rect.height() < 200:
                continue
            if ctrl.IsValuePatternAvailable:
                self._input_control = ctrl
                log.info("聊天输入框: %dx%d (ValuePattern)", rect.width(), rect.height())
                break

        if not self._input_control:
            self._input_control = candidates[0] if candidates else edits[0]
            log.warning("输入框无 ValuePattern，使用剪贴板后备")

        return True

    def send_text(self, contact: str, text: str) -> bool:
        """发送文本消息。跳过联系人切换，直接在当前窗口发送。"""
        with self._lock:
            if not self._ready:
                log.error("UIA not ready")
                return False
            if not self._ensure_window():
                return False
            if "<PIL." in text:
                return False

            self._activate()

            if not self._locate_input():
                return False

            try:
                if self._use_coord_fallback:
                    import ctypes
                    from ctypes import wintypes
                    hwnd = ctypes.windll.user32.FindWindowW('Qt51514QWindowIcon', None)
                    if not hwnd:
                        hwnd = ctypes.windll.user32.FindWindowW('WeChatMainWndForPC', None)
                    if hwnd:
                        rect = wintypes.RECT()
                        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
                        win_w = rect.right - rect.left
                        win_h = rect.bottom - rect.top
                        input_x = rect.left + int(win_w * 0.3)
                        input_y = rect.top + int(win_h * 0.92)
                        ctypes.windll.user32.SetCursorPos(input_x, input_y)
                        ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
                        ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
                    time.sleep(0.3)
                    import pyperclip
                    pyperclip.copy(text)
                    time.sleep(0.05)
                    self._auto.SendKeys('{Ctrl}v')
                    time.sleep(0.3)
                    self._auto.SendKeys('{Enter}')
                    log.info("[UIA✓] %s: %s...", contact, text[:50])
                    return True

                ctrl = self._input_control
                if ctrl.IsValuePatternAvailable:
                    try:
                        ctrl.SetValue("")
                    except Exception:
                        pass
                    try:
                        ctrl.SetValue(text)
                    except Exception:
                        import pyperclip
                        pyperclip.copy(text)
                        time.sleep(0.05)
                        ctrl.SendKeys('{Ctrl}a')
                        ctrl.SendKeys('{Ctrl}v')
                else:
                    import pyperclip
                    pyperclip.copy(text)
                    ctrl.SendKeys('{Ctrl}a')
                    time.sleep(0.05)
                    ctrl.SendKeys('{Ctrl}v')

                time.sleep(0.1)
                if self._send_button:
                    self._send_button.Click()
                else:
                    ctrl.SendKeys('{Enter}')

                log.info("[UIA✓] %s: %s...", contact, text[:50])
                return True
            except Exception as e:
                log.error("[UIA✗] %s: %s", contact, e)
                return False
