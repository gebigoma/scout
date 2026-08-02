import json
import logging

from . import paths


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "stage": getattr(record, "stage", None),
            "message": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def get_logger(run_date: str) -> logging.Logger:
    logger = logging.getLogger(f"scout.{run_date}")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.FileHandler(paths.logs_dir() / f"{run_date}.jsonl")
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False
    return logger


def log(logger: logging.Logger, stage: str, message: str, **fields) -> None:
    logger.info(message, extra={"stage": stage, "extra_fields": fields})
