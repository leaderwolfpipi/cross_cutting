"""Where today's supplier quote comes from.

Edit ``source.json`` in this directory. ``url`` wins when it is set; otherwise the
quote text is read from ``file``. Replace that file each day. Prices are never
hard-coded in Python.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

QUOTES_DIR = Path(__file__).resolve().parent
CONFIG_PATH = QUOTES_DIR / "source.json"
MAX_CHARS = 300_000


def load_quote_text(config_path: Path | None = None) -> str:
    path = config_path or CONFIG_PATH
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取报价配置 {path.name}：{exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("报价配置必须是包含 file 和 url 的对象")
    url = str(config.get("url") or "").strip()
    if url:
        return _fetch(url)
    file_name = str(config.get("file") or "latest.txt").strip()
    quote_path = Path(file_name)
    if not quote_path.is_absolute():
        quote_path = path.parent / quote_path
    if not quote_path.is_file():
        raise ValueError(f"报价文件不存在：{quote_path}")
    try:
        text = quote_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"无法读取报价文件 {quote_path.name}：{exc}") from exc
    if len(text) > MAX_CHARS:
        raise ValueError("报价原文超过 30 万字")
    return text


def _fetch(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("报价地址只支持 http 或 https")
    request = urllib.request.Request(url, headers={"User-Agent": "seafood-quote/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            # MAX_CHARS is a character limit. A Chinese UTF-8 character can occupy
            # three bytes (four for some Unicode characters), so cap bytes separately.
            raw = response.read(MAX_CHARS * 4 + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"无法从报价地址读取：{exc}") from exc
    if len(raw) > MAX_CHARS * 4:
        raise ValueError("报价原文超过 30 万字")
    try:
        text = raw.decode(charset)
    except (UnicodeError, LookupError) as exc:
        raise ValueError(f"报价地址返回的文本编码无法解析：{exc}") from exc
    if len(text) > MAX_CHARS:
        raise ValueError("报价原文超过 30 万字")
    return text
