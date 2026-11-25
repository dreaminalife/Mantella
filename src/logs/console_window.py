"""Which window Mantella.exe shows in. In the classic Windows console, clicking in the window or selecting text freezes its text until
Esc or Enter (Mantella itself keeps running: see window_writer). Windows Terminal doesn't freeze, and Windows can be set to open
every console program in it"""
import sys

WINDOWS_TERMINAL_TIP = ("Tip: clicking this window freezes its text until you press Esc (Mantella keeps running). To select and copy text "
                        "without that, have Windows open programs in Windows Terminal: Settings > System > For developers > Terminal > "
                        "Windows Terminal.")


def is_classic_console() -> bool:
    """Whether Mantella shows in the classic Windows console. Not in Windows Terminal or another terminal: they keep the console
    window Windows makes hidden, and show the text in their own. Not told by WT_SESSION: a program started from Windows Terminal passes
    it on, also to a Mantella the game opens in a classic console"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        user32 = ctypes.WinDLL("user32")
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetConsoleWindow.restype = ctypes.c_void_p
        user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        window = kernel32.GetConsoleWindow()
        return bool(window) and bool(user32.IsWindowVisible(window))
    except Exception:
        return False
