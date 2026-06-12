"""
UIA 发送器 — 精确定位微信窗口，支持 @mention。
"""
import logging, threading, time
log = logging.getLogger(__name__)


class UiaSender:
    def __init__(self):
        self._lock = threading.Lock()
        self._ready = False
        self._auto = None
        self._wechat_window = None
        self._init()

    def _init(self):
        import uiautomation as auto
        self._auto = auto
        root = auto.GetRootControl()
        for w in root.GetChildren():
            name = w.Name or ""
            if "微信" in name or "WeChat" in name:
                self._wechat_window = w
                self._ready = True
                log.info("WeChat found: '%s' cls=%s", name, w.ClassName)
                return
        # fallback by class
        for cls in ("Qt51514QWindowIcon", "CefTopWindow", "WeChatMainWndForPC"):
            try:
                w = auto.WindowControl(ClassName=cls, searchDepth=1)
                if w.Exists(1):
                    self._wechat_window = w
                    self._ready = True
                    log.info("WeChat found by cls=%s", cls)
                    return
            except Exception:
                pass

    def send_text(self, contact: str, text: str, at_sender: str = "") -> bool:
        with self._lock:
            if not self._ready or not self._wechat_window:
                self._init()
                if not self._ready:
                    return False
            try:
                import ctypes, pyperclip

                # 用 uiautomation 激活微信窗口（更可靠）
                try:
                    self._wechat_window.SetFocus()
                    self._wechat_window.SetActive()
                except Exception:
                    pass
                time.sleep(0.3)

                # 确保前台
                try:
                    hwnd = self._wechat_window.NativeWindowHandle
                    TID = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, None)
                    CTID = ctypes.windll.kernel32.GetCurrentThreadId()
                    ctypes.windll.user32.AttachThreadInput(CTID, TID, True)
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
                    ctypes.windll.user32.BringWindowToTop(hwnd)
                    time.sleep(0.3)
                except Exception:
                    pass

                if at_sender:
                    self._auto.SendKeys('@')
                    time.sleep(0.3)
                    self._auto.SendKeys(at_sender)
                    time.sleep(0.4)
                    self._auto.SendKeys('{Enter}')
                    time.sleep(0.3)
                    self._auto.SendKeys(' ')
                    time.sleep(0.1)

                pyperclip.copy(text)
                time.sleep(0.05)
                self._auto.SendKeys('{Ctrl}v')
                time.sleep(0.3)
                self._auto.SendKeys('{Enter}')

                try:
                    ctypes.windll.user32.AttachThreadInput(CTID, TID, False)
                except Exception:
                    pass
                log.info("Sent: @%s %s...", at_sender or "no-at", text[:40])
                return True
            except Exception as e:
                log.error("Send failed: %s", e)
                return False
