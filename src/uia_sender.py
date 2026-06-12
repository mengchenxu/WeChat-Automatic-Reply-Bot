"""
UIA 发送器 — 用 ctypes keybd_event 直接模拟键盘。
"""
import ctypes, logging, threading, time
log = logging.getLogger(__name__)


VK_CONTROL = 0x11
VK_V = 0x56
VK_RETURN = 0x0D
VK_AT = 0x32  # Shift+2 on US keyboard
VK_SHIFT = 0x10

def _press(key):
    ctypes.windll.user32.keybd_event(key, 0, 0, 0)

def _release(key):
    ctypes.windll.user32.keybd_event(key, 0, 2, 0)

def _tap(key):
    _press(key); _release(key)

def _paste():
    """Ctrl+V"""
    _press(VK_CONTROL); _tap(VK_V); _release(VK_CONTROL)

def _enter():
    _tap(VK_RETURN)

def _type_at():
    """Shift+2 = @"""
    _press(VK_SHIFT); _tap(VK_AT); _release(VK_SHIFT)

def _type_text(text: str):
    """逐个字符输入（用于 @选人）。"""
    import pyperclip
    pyperclip.copy(text)
    time.sleep(0.05)
    _paste()


def _focus_wechat():
    """激活微信窗口。"""
    for cls in ('Qt51514QWindowIcon', 'WeChatMainWndForPC', 'CefTopWindow'):
        hwnd = ctypes.windll.user32.FindWindowW(cls, None)
        if hwnd:
            TID = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, None)
            CTID = ctypes.windll.kernel32.GetCurrentThreadId()
            ctypes.windll.user32.AttachThreadInput(CTID, TID, True)
            ctypes.windll.user32.ShowWindow(hwnd, 9)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            ctypes.windll.user32.BringWindowToTop(hwnd)
            time.sleep(0.5)
            return True
    return False


class UiaSender:
    def __init__(self):
        self._lock = threading.Lock()
        self._ready = True

    def send_text(self, contact: str, text: str, at_sender: str = "") -> bool:
        with self._lock:
            if not _focus_wechat():
                log.error("WeChat window not found")
                return False
            try:
                if at_sender:
                    _type_at()            # @
                    time.sleep(0.3)
                    _type_text(at_sender) # 输入名字
                    time.sleep(0.5)
                    _enter()              # 选中
                    time.sleep(0.3)
                    _type_at()            # 再输入 @ 或空格
                    _release(VK_SHIFT)
                    time.sleep(0.1)
                    # 这里不输入空格，直接用下面的粘贴

                import pyperclip
                pyperclip.copy(text)
                time.sleep(0.1)
                _paste()                  # Ctrl+V 粘贴回复
                time.sleep(0.3)
                _enter()                  # Enter 发送

                log.info("Sent: @%s %s...", at_sender or "no-at", text[:40])
                return True
            except Exception as e:
                log.error("Send failed: %s", e)
                return False
