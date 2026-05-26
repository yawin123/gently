import sys
from datetime import datetime

_LOG_PATH = "/tmp/gently.log"
_USE_COLOR = sys.stdout.isatty()

_COLOR = {
    "DEBUG": "\033[36m",
    "INFO":  "\033[32m",
    "WARN":  "\033[33m",
    "ERROR": "\033[31m",
    "RESET": "\033[0m",
}

_handle = None


def _open_log() -> None:
    global _handle
    if _handle is None:
        _handle = open(_LOG_PATH, "a", encoding="utf-8")


def _write(level: str, message: str) -> None:
    _open_log()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    plain = f"[{ts}] [{level:<5}] {message}"
    if _USE_COLOR:
        c = _COLOR.get(level, "")
        r = _COLOR["RESET"]
        print(f"{c}[{ts}] [{level:<5}]{r} {message}", flush=True)
    else:
        print(plain, flush=True)
    _handle.write(plain + "\n")
    _handle.flush()


def debug(message: str) -> None:
    _write("DEBUG", message)


def info(message: str) -> None:
    _write("INFO", message)


def warn(message: str) -> None:
    _write("WARN", message)


def error(message: str) -> None:
    _write("ERROR", message)
