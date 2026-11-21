import logging
import sys
from contextvars import ContextVar

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get("") or "-"
        return True


def configure_logging(level: str = "INFO") -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not root_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] [request_id=%(request_id)s] %(message)s"
        )
        handler.setFormatter(formatter)
        handler.addFilter(RequestIdFilter())
        root_logger.addHandler(handler)
    else:
        for handler in root_logger.handlers:
            if not any(isinstance(f, RequestIdFilter) for f in handler.filters):
                handler.addFilter(RequestIdFilter())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
