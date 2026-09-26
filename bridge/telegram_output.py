"""Normalize completed model output for plain-text Telegram delivery."""

from __future__ import annotations

import re
from html.parser import HTMLParser

_CODE = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`")
_AUTOLINK = re.compile(r"<(?:https?://[^>\s]+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})>")
_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "div",
    "dl",
    "dt",
    "dd",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
_SKIP_CONTENT = {"script", "style"}


class _PlainTelegramHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.links: list[tuple[str, int]] = []

    def _newline(self) -> None:
        if not self.parts or self.parts[-1].endswith(("\n", " ", "\t")):
            return
        self.parts.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.casefold()
        if name in _SKIP_CONTENT:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if name == "a":
            href = next((str(value or "") for key, value in attrs if key.casefold() == "href"), "").strip()
            self.links.append((href if href.startswith(("https://", "http://")) else "", len(self.parts)))
        if name == "br":
            self._newline()
        elif name == "li":
            self._newline()
            self.parts.append("- ")
        elif name in _BLOCK_TAGS:
            self._newline()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        name = tag.casefold()
        if name in _SKIP_CONTENT:
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if name == "a":
            href, start = self.links.pop() if self.links else ("", len(self.parts))
            visible = "".join(self.parts[start:]).strip()
            if href and href not in visible:
                self.parts.append(f" ({href})")
        if name in _BLOCK_TAGS or name == "li":
            self._newline()

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)


def telegram_safe_output(text: str) -> str:
    """Strip presentation HTML outside code while preserving readable structure."""
    source = str(text or "")
    if "<" not in source and "&" not in source:
        return source
    protected: list[str] = []

    def protect(match: re.Match[str]) -> str:
        token = f"\x00TGCODE{len(protected)}\x00"
        protected.append(match.group(0))
        return token

    masked = _CODE.sub(protect, source)
    masked = _AUTOLINK.sub(protect, masked)
    parser = _PlainTelegramHTML()
    try:
        parser.feed(masked)
        parser.close()
        result = "".join(parser.parts)
    except Exception:
        return source
    result = re.sub(r"[ \t]+\n", "\n", result)
    result = re.sub(r"\n(?=[.,!?;:])", "", result)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    for index, value in enumerate(protected):
        result = result.replace(f"\x00TGCODE{index}\x00", value)
    return result
