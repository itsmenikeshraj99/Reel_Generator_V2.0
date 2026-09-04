import logging
import sys

_CONFIGURED = False


def _configure_root() -> None:
    """Configure the root logger exactly once per process."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    # Wipe any default handlers (uvicorn installs its own — keep them, but add ours)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    _CONFIGURED = True


_configure_root()


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
