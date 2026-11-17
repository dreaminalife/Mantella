"""Short notices about the voice services for the player in game, shown once each with the next reply to the game"""
import threading

_notices: list[str] = []
_shown: set[str] = set()
_lock = threading.Lock()


def add_notice(text: str) -> None:
    with _lock:
        if text not in _shown:
            _shown.add(text)
            _notices.append(text)


def take_notices() -> list[str]:
    with _lock:
        taken = list(_notices)
        _notices.clear()
        return taken
