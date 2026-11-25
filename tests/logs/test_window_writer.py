import logging
import threading
import time

from src.logs.window_writer import WindowWriter


class FrozenWindow:
    """A window like the classic Windows console while text is selected: writing waits until the selection ends"""

    def __init__(self) -> None:
        self.selection_ended = threading.Event()
        self.lines: list[str] = []

    def write(self, text: str) -> None:
        self.selection_ended.wait()
        self.lines.append(text)

    def flush(self) -> None:
        pass


def wait_for(condition, seconds: float = 2.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def a_logger(handler: logging.Handler) -> logging.Logger:
    log = logging.getLogger(f"test_window_writer_{id(handler)}")
    log.propagate = False
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    return log


def test_logging_goes_on_while_the_window_is_frozen():
    window = FrozenWindow()
    writer = WindowWriter(window)
    log = a_logger(writer)

    started = time.time()
    for n in range(20):
        log.info(f"line {n}")
    assert time.time() - started < 1.0 # nobody waited for the window
    assert window.lines == []

    window.selection_ended.set()
    assert wait_for(lambda: len(window.lines) == 20)
    assert window.lines == [f"line {n}\n" for n in range(20)] # the window catches up, in order
    writer.close()


def test_closing_doesnt_wait_long_for_a_frozen_window():
    window = FrozenWindow()
    writer = WindowWriter(window)
    a_logger(writer).info("never shown")

    started = time.time()
    writer.close()
    assert time.time() - started < 5.0
    window.selection_ended.set()
