"""发送阶段 — UIA 内联 @mention 发送"""
import logging
import re
import time

from src.decode import DecodedReply

logger = logging.getLogger(__name__)

# UIA 键盘码
VK_CONTROL = 0x11
VK_V = 0x56
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_AT = 0x32  # Shift+2


def _press(key): import ctypes; ctypes.windll.user32.keybd_event(key, 0, 0, 0)
def _release(key): import ctypes; ctypes.windll.user32.keybd_event(key, 0, 2, 0)
def _tap(key): _press(key); _release(key)
def _paste():
    import pyperclip; _press(VK_CONTROL); _tap(VK_V); _release(VK_CONTROL)
def _enter(): _tap(VK_RETURN)
def _type_at(): _press(VK_SHIFT); _tap(VK_AT); _release(VK_SHIFT)


def _at_mention(name: str):
    """模拟键盘 @选人。"""
    _type_at()
    time.sleep(0.2)
    import pyperclip
    pyperclip.copy(name)
    time.sleep(0.05)
    _paste()
    time.sleep(0.5)
    _enter()


def _focus_wechat() -> bool:
    """激活微信窗口。"""
    import ctypes
    try:
        hwnd_console = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd_console:
            ctypes.windll.user32.ShowWindow(hwnd_console, 6)
    except Exception:
        pass
    time.sleep(0.1)

    for cls in ('Qt51514QWindowIcon', 'WeChatMainWndForPC', 'CefTopWindow',
                'Qt51524QWindowIcon', 'Qt51522QWindowIcon'):
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


def send(reply: DecodedReply, room_id: str, at_sender: str) -> bool:
    """内联 @mention 发送。at_sender 开头，正文 @在出现位置实时转键盘@。"""
    import threading
    lock = threading.Lock()

    with lock:
        if not _focus_wechat():
            logger.error("WeChat window not found")
            return False
        try:
            # 1. 开头 @回复对象
            if at_sender:
                _at_mention(at_sender.strip())
                time.sleep(0.3)

            # 2. 内联 @mention：按 @名字 拆分，交替粘贴文字和 @人
            text = reply.clean_text
            pattern = r'@([a-zA-Z][a-zA-Z0-9 ]*(?:\s+[a-zA-Z][a-zA-Z0-9 ]*)*|[一-鿿぀-ゟ가-힯]{2,4})'
            segments = re.split(pattern, text)

            inline_count = 0
            import pyperclip
            for i, seg in enumerate(segments):
                if i % 2 == 0:
                    if seg.strip():
                        pyperclip.copy(seg)
                        time.sleep(0.05)
                        _paste()
                        time.sleep(0.3)
                else:
                    _at_mention(seg)
                    inline_count += 1
                    time.sleep(0.3)

            _enter()

            at_info = f"@({at_sender})" if at_sender else "no-at"
            if inline_count:
                at_info += f" +{inline_count} inline"
            logger.info("Sent: %s -> %s...", at_info, text[:60])
            return True
        except Exception as e:
            logger.error("Send failed: %s", e)
            return False
