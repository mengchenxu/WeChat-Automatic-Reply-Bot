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
    """激活微信 — 最小化控制台，避免焦点被抢。"""
    # 最小化控制台窗口（而不是 FreeConsole 销毁它）
    try:
        hwnd_console = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd_console:
            ctypes.windll.user32.ShowWindow(hwnd_console, 6)  # SW_MINIMIZE
    except Exception:
        pass
    time.sleep(0.1)

    # 尝试多种窗口类名（适配不同微信版本）
    for cls in ('Qt51514QWindowIcon', 'WeChatMainWndForPC', 'CefTopWindow', 'Qt51524QWindowIcon', 'Qt51522QWindowIcon'):
        hwnd = ctypes.windll.user32.FindWindowW(cls, None)
        if hwnd:
            TID = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, None)
            CTID = ctypes.windll.kernel32.GetCurrentThreadId()
            ctypes.windll.user32.AttachThreadInput(CTID, TID, True)
            ctypes.windll.user32.ShowWindow(hwnd, 1)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            ctypes.windll.user32.BringWindowToTop(hwnd)
            time.sleep(0.5)
            ctypes.windll.user32.AttachThreadInput(CTID, TID, False)
            return True
    return False


class UiaSender:
    def __init__(self):
        self._lock = threading.Lock()
        self._ready = True

    def _at_mention(self, name: str):
        """模拟键盘 @某人 并选中。"""
        _type_at()            # Shift+2 = @
        time.sleep(0.2)
        _type_text(name)      # 粘贴名字
        time.sleep(0.5)
        _enter()              # Enter 从列表中选中

    def send_text(self, contact: str, text: str, at_sender: str = "", at_mentions: list = None) -> bool:
        """
        发送群聊消息，支持内联 @mention。
        at_sender: 回复对象（消息开头自动 @）
        text: 消息正文，其中 @name 会被实时转换为真实 @mention
        """
        with self._lock:
            if not _focus_wechat():
                log.error("WeChat window not found")
                return False
            try:
                # 1. 开头 @回复对象
                if at_sender:
                    self._at_mention(at_sender.strip())
                    time.sleep(0.3)

                # 2. 内联 @mention：按 @名字 切分，交替粘贴文字和 @人
                import re as _re
                # 匹配 @拉丁名（可含空格）或 @中文名（2-4字）
                _pat = r'@([a-zA-Z][a-zA-Z0-9 ]*(?:\s+[a-zA-Z][a-zA-Z0-9 ]*)*|[一-鿿぀-ゟ가-힯]{2,4})'

                segments = _re.split(_pat, text)
                # segments = [text0, name1, text1, name2, text2, ...]
                # 偶数索引 = 纯文本，奇数索引 = 要 @ 的名字

                inline_count = 0
                for i, seg in enumerate(segments):
                    if i % 2 == 0:
                        # 纯文本段
                        if seg.strip():
                            import pyperclip
                            pyperclip.copy(seg)
                            time.sleep(0.05)
                            _paste()
                            time.sleep(0.3)
                    else:
                        # @mention 段 — 模拟键盘 @选人
                        self._at_mention(seg)
                        inline_count += 1
                        time.sleep(0.3)

                _enter()

                at_info = f"@({at_sender})" if at_sender else "no-at"
                if inline_count:
                    at_info += f" +{inline_count} inline"
                log.info("Sent: %s -> %s...", at_info, text[:60])
                return True
            except Exception as e:
                log.error("Send failed: %s", e)
                return False
