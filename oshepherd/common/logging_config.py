import logging
import sys

_CONFIGURED = False


def configure_logging(service: str, level: str = "info") -> None:
    """Configure lightweight process-wide logging for oshepherd services."""
    global _CONFIGURED

    normalized_level = getattr(logging, str(level or "info").upper(), logging.INFO)

    if _CONFIGURED:
        logging.getLogger().setLevel(normalized_level)
        logging.getLogger("oshepherd").setLevel(normalized_level)
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(normalized_level)

    logging.getLogger("oshepherd").setLevel(normalized_level)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    _CONFIGURED = True

    logging.getLogger(__name__).info(
        "logging configured service=%s level=%s",
        service,
        logging.getLevelName(normalized_level),
    )
