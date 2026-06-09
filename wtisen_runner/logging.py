import json
import logging
import re


def sanitize_text(text: str | None) -> str:
    if not text:
        return ""
    out = text
    out = re.sub(r"[\w\.-]+@[\w\.-]+\.\w+", "<redacted-email>", out)
    out = re.sub(r"\b[A-Za-z0-9._%+-]{20,}\b", "<redacted-token>", out)
    out = re.sub(r"(?i)(password|passwd|token|secret)\s*[:=]\s*\S+", r"\1=<redacted>", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup_logging(debug: bool = False, json_logs: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    handler = logging.StreamHandler()
    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
