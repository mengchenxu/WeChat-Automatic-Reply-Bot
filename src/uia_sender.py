"""
UIA 发送器 — 剪贴板 + 键盘方式，适配微信 4.x Qt 版。
"""
import logging, threading, time
log = logging.getLogger(__name__)


class UiaSender:
    def __init__(self, search_enabled: bool = False):
        self._lock = threading.Lock()
        self._ready = False
        self._auto = None
        self._init()

    def _init(self):
        import uiautomation as auto
        self._auto = auto
        root = auto.GetRootControl()
        for w in root.GetChildren():
            if "微信" in w.Name or "WeChat" in w.Name:
                self._ready = True
                log.info("WeChat window: '%s' ClassName=%s", w.Name, w.ClassName)
                return
        for cls in ("Qt51514QWindowIcon", "CefTopWindow", "WeChatMainWndForPC"):
            try:
                w = auto.WindowControl(ClassName=cls, searchDepth=1)
                if w.Exists(1):
                    self._ready = True
                    log.info("WeChat window: ClassName=%s", cls)
                    return
            except Exception:
                pass

    def send_text(self, contact: str, text: str) -> bool:
        with self._lock:
            if not self._ready:
                return False
            if "<PIL." in text:
                return False
            try:
                import ctypes, pyperclip
                hwnd = ctypes.windll.user32.FindWindowW('Qt51514QWindowIcon', None)
                if not hwnd:
                    hwnd = ctypes.windll.user32.FindWindowW('WeChatMainWndForPC', None)
                if not hwnd:
                    return False

                TID = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, None)
                CTID = ctypes.windll.kernel32.GetCurrentThreadId()
                ctypes.windll.user32.AttachThreadInput(CTID, TID, True)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                time.sleep(0.2)

                pyperclip.copy(text)
                time.sleep(0.05)
                self._auto.SendKeys('{Ctrl}v')
                time.sleep(0.3)
                self._auto.SendKeys('{Enter}')

                ctypes.windll.user32.AttachThreadInput(CTID, TID, False)
                log.info("Sent: %s...", text[:50])
                return True
            except Exception as e:
                log.error("Send failed: %s", e)
                return False
