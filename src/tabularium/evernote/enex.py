"""Reading Evernote exports (ENEX) and the notes' ENML content.

An ``.enex`` file holds one or more ``<note>`` elements: title, dates, tags, the note body
as ENML (an XHTML dialect, inside CDATA) and the attachments as base64 ``<resource>``s.
The body refers to an attachment by the MD5 of its bytes (``<en-media hash="…">``).
"""

import base64
import binascii
import hashlib
import html
import re
import xml.etree.ElementTree as ET  # noqa: S405 — see read_notes
from collections.abc import Iterator
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from tabularium.fsutil import lp


@dataclass(frozen=True)
class Resource:
    data: bytes
    mime: str
    file_name: str | None

    @property
    def md5(self) -> str:
        return hashlib.md5(self.data, usedforsecurity=False).hexdigest()


@dataclass(frozen=True)
class Note:
    title: str
    created: str | None  # as written in the file, e.g. 20210304T101500Z; identity only
    tags: tuple[str, ...]
    content: str  # ENML
    resources: tuple[Resource, ...]
    problems: tuple[str, ...] = ()  # e.g. an attachment that could not be read

    @property
    def fingerprint(self) -> str:
        """Identifies the note across exports, so a second import of it is recognised."""
        digest = hashlib.sha256()
        for part in (self.title, self.created or "", self.content):
            digest.update(part.encode("utf-8"))
            digest.update(b"\0")
        for resource in self.resources:
            digest.update(resource.md5.encode("ascii"))
        return digest.hexdigest()


def _text(element: ET.Element, tag: str) -> str | None:
    child = element.find(tag)
    return child.text if child is not None and child.text is not None else None


def _resource(element: ET.Element) -> Resource | str | None:
    """The attachment, or what is wrong with it."""
    data = _text(element, "data")
    if not data:
        return None
    file_name = None
    attributes = element.find("resource-attributes")
    if attributes is not None:
        file_name = (_text(attributes, "file-name") or "").strip() or None
    mime = (_text(element, "mime") or "application/octet-stream").strip().lower()
    try:
        return Resource(base64.b64decode("".join(data.split()), validate=True), mime, file_name)
    except (binascii.Error, ValueError):
        return f"attachment {file_name or mime} is damaged in the export"


def read_notes(path: Path) -> Iterator[Note]:
    """The notes in an ENEX file, one at a time (exports can be large)."""
    # The files come from the user's own Evernote export. ElementTree resolves no external
    # entities or DTDs, and current expat limits entity expansion.
    with open(lp(path), "rb") as stream:
        for _event, element in ET.iterparse(stream, events=("end",)):  # noqa: S314
            if element.tag != "note":
                continue
            read = [_resource(e) for e in element.iterfind("resource")]
            resources = tuple(r for r in read if isinstance(r, Resource))
            problems = tuple(r for r in read if isinstance(r, str))
            yield Note(
                title=(_text(element, "title") or "").strip(),
                created=_text(element, "created"),
                tags=tuple(
                    t.text.strip() for t in element.iterfind("tag") if t.text and t.text.strip()
                ),
                content=_text(element, "content") or "",
                resources=resources,
                problems=problems,
            )
            element.clear()


# --- ENML ------------------------------------------------------------------------------

# Rendered when a text note is printed; every other tag is dropped (its text is kept), and
# no attribute is kept at all, so nothing in a note can load anything when it is printed.
_KEEP = frozenset(
    {
        *("p", "div", "br", "hr", "span", "b", "strong", "i", "em", "u", "s", "strike"),
        *("sub", "sup", "small", "big", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li"),
        *("dl", "dt", "dd", "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption"),
        *("blockquote", "pre", "code", "font", "center"),
    }
)
_VOID = frozenset({"br", "hr"})
_DROP = frozenset({"script", "style", "title", "head", "object", "iframe", "embed", "svg"})
_BLOCK = frozenset(
    {
        *("p", "div", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li"),
        *("dl", "dt", "dd", "table", "tr", "blockquote", "pre"),
    }
)


class _Enml(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.html: list[str] = []
        self.media: list[str] = []  # en-media hashes, in order
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROP:
            self._skip += 1
            return
        if self._skip:
            return
        values = dict(attrs)
        if tag == "en-media":
            if values.get("hash"):
                self.media.append(values["hash"].lower())
            return
        if tag == "en-todo":
            box = "☑ " if (values.get("checked") or "").lower() == "true" else "☐ "
            if self.text and not self.text[-1].endswith("\n"):
                self.text.append("\n")  # each to-do on its own line
            self.text.append(box)
            self.html.append(box)
            return
        if tag == "en-crypt":
            self._skip += 1  # encrypted text cannot be read here
            self.text.append("[encrypted text]")
            self.html.append("[encrypted text]")
            return
        if tag in _BLOCK:
            self.text.append("\n")
        if tag == "td" or tag == "th":
            self.text.append("\t")
        if tag in _KEEP:
            self.html.append(f"<{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag in ("en-crypt", *_DROP):
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP or tag == "en-crypt":
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag in _BLOCK:
            self.text.append("\n")
        if tag in _KEEP and tag not in _VOID:
            self.html.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self.text.append(data)
        self.html.append(html.escape(data, quote=False))


@dataclass(frozen=True)
class Body:
    text: str  # plain text, for the document's note
    html: str  # safe markup for printing
    media: tuple[str, ...]  # MD5 hashes of the attachments, in the order they appear


def parse_body(enml: str) -> Body:
    parser = _Enml()
    parser.feed(enml)
    parser.close()
    lines = (re.sub(r"[ \t ]+", " ", line).strip() for line in "".join(parser.text).split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return Body(text, "".join(parser.html), tuple(parser.media))
