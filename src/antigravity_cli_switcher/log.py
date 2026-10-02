import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_logger = None


def get_logger(root_dir: Path | None = None) -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    if root_dir is None:
        root_dir = Path(os.environ.get("ACS_ROOT", "~/.antigravity-cli-switcher")).expanduser()

    logger = logging.getLogger("antigravity_cli_switcher")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    log_dir = root_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "manager.log"

    # Rotate every midnight, keep 7 days
    handler = TimedRotatingFileHandler(
        filename=str(log_file), when="midnight", interval=1, backupCount=7, encoding="utf-8"
    )
    handler.suffix = "%Y-%m-%d"

    formatter = logging.Formatter(fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    handler.setFormatter(formatter)

    logger.addHandler(handler)
    _logger = logger
    return logger
