import logging
import re

_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(ghp_|github_pat_|sk-ant-)[A-Za-z0-9_\-]+"),
)


class RedactSecrets(logging.Filter):
    """Belt and braces: even if a token reaches a log line, it leaves redacted."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        redacted = msg
        for pat in _SECRET_PATTERNS:
            redacted = pat.sub(lambda m: (m.group(1) if m.lastindex else "") + "***", redacted)
        if redacted != msg:
            record.msg, record.args = redacted, ()
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactSecrets())
    root.addHandler(handler)
    root.setLevel(level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)
