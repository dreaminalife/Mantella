"""Mantella.exe's window, written from a thread of its own. In the classic Windows console, clicking in the window or selecting text
freezes it until Esc or Enter, and anything writing to it waits. Only this thread waits then: Mantella goes on answering the game and
the settings page, logging.log is still written, and the window catches up once the selection ends"""
import logging
import queue
import sys
import threading

CLOSE_WAIT_SECONDS = 2.0 # on closing, how long the lines not shown yet get: a frozen window doesn't hold up Mantella closing


class WindowWriter(logging.Handler):
    def __init__(self, stream=None) -> None:
        """stream: where the lines go. The window (stderr, as logging.StreamHandler) when None"""
        super().__init__()
        self.__stream = stream
        self.__lines: queue.Queue[str | None] = queue.Queue()
        self.__thread = threading.Thread(target=self.__write_lines, name="Mantella window", daemon=True)
        self.__thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.__lines.put(self.format(record)) # formatted here: the line as it was when it was logged
        except Exception:
            self.handleError(record)

    def __write_lines(self) -> None:
        while True:
            line = self.__lines.get()
            try:
                if line is None:
                    return
                stream = self.__stream or sys.stderr
                if stream is None: # no window
                    continue
                stream.write(line + "\n")
                stream.flush()
            except Exception:
                pass # the window can't show it: logging.log still has it
            finally:
                self.__lines.task_done()

    def close(self) -> None:
        if self.__thread.is_alive():
            self.__lines.put(None)
            self.__thread.join(CLOSE_WAIT_SECONDS)
        super().close()
