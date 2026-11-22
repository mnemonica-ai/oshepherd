"""Worker-only console and bounded private file logging."""

from contextlib import contextmanager
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import stat
import sys

from .private_files import private_directory

LOGGER_NAME = "oshepherd.backends.supabase.worker"


class PrivateRotatingFileHandler(RotatingFileHandler):
    """Preserve private permissions on the initial file and after every rotation."""

    def _open(self):
        descriptor = os.open(
            self.baseFilename,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise ValueError("Worker log must be an owned private regular file")
            return os.fdopen(descriptor, "a", encoding="utf-8")
        except BaseException:
            os.close(descriptor)
            raise


@contextmanager
def worker_logging(session_file, level="info"):
    """Do not enable HTTP library logging or change the process's root logger."""
    session_path = Path(session_file).absolute()
    path = (
        session_path.with_suffix(".log")
        if session_path.suffix == ".json"
        else session_path.with_name(session_path.name + ".log")
    )
    private_directory(path.parent)
    file_handler = PrivateRotatingFileHandler(
        path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    console_handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    for handler in (console_handler, file_handler):
        handler.setFormatter(formatter)
    logger = logging.getLogger(LOGGER_NAME)
    previous = logger.handlers[:], logger.level, logger.propagate
    logger.handlers = [console_handler, file_handler]
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.propagate = False
    try:
        logger.info("Worker lifecycle log: %s", path)
        yield path
    finally:
        logger.handlers, previous_level, logger.propagate = previous
        logger.setLevel(previous_level)
        for handler in (console_handler, file_handler):
            handler.close()
