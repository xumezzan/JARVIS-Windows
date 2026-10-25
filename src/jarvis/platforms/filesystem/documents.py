"""Text extraction only; no program launch, active content, OCR or network retrieval."""

import io
import zipfile
from pathlib import Path
from typing import Literal

from jarvis.platforms.filesystem.storage import directory, read
from jarvis.security.filesystem_policy import MAX_TEXT, READ_EXTENSIONS, FilePolicy
from jarvis.tools.base import ToolError
from jarvis.tools.filesystem import DocumentResult, ReadDocument


def extract(
    data: bytes, extension: str
) -> tuple[str, bool, Literal["", "no_text_layer", "partial_document"]]:
    text = ""
    truncated = False
    if extension == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data), strict=True)
        try:
            if reader.is_encrypted:
                raise ToolError("file_unsupported")
            total = len(reader.pages)
            for index in range(min(total, 100)):
                text += (reader.pages[index].extract_text() or "") + "\n"
                if len(text) > MAX_TEXT:
                    truncated = True
                    break
            truncated = truncated or total > 100
            return (
                text[:MAX_TEXT],
                truncated,
                "no_text_layer" if not text.strip() else "partial_document" if truncated else "",
            )
        finally:
            reader.close()
    if extension == ".docx":
        partial = False
        # Inspect central-directory budgets before the OPC parser inflates package members.
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if (
                len(members) > 2048
                or sum(item.file_size for item in members) > 32 * 1024 * 1024
                or any(
                    item.file_size > 8 * 1024 * 1024
                    or item.file_size > max(1, item.compress_size) * 200
                    for item in members
                )
            ):
                raise ToolError("file_limit")
            if any(item.filename.endswith("vbaProject.bin") for item in members):
                raise ToolError("file_unsupported")
            partial = any(
                item.filename.startswith(
                    (
                        "word/header",
                        "word/footer",
                        "word/footnotes",
                        "word/endnotes",
                        "word/comments",
                    )
                )
                for item in members
            )
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        document = Document(io.BytesIO(data))
        partial = partial or any(
            marker in document.element.xml for marker in ("<w:ins ", "<w:del ", "<w:txbxContent")
        )
        for block in document.iter_inner_content():
            if isinstance(block, Paragraph):
                text += block.text + "\n"
            elif isinstance(block, Table):
                for row in block.rows:
                    partial = partial or any(cell.tables for cell in row.cells)
                    text += "\t".join(cell.text for cell in row.cells) + "\n"
                    if len(text) > MAX_TEXT:
                        break
            if len(text) > MAX_TEXT:
                truncated = True
                break
        return text[:MAX_TEXT], truncated, "partial_document" if truncated or partial else ""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16")
    else:
        text = data.decode("utf-8-sig")
    if "\x00" in text:
        raise ToolError("file_unsupported")
    return text[:MAX_TEXT], len(text) > MAX_TEXT, "partial_document" if len(text) > MAX_TEXT else ""


def read_document(policy: FilePolicy, args: ReadDocument) -> DocumentResult:
    path: Path = policy.path(args.path)
    if path.suffix.casefold() not in READ_EXTENSIONS:
        raise ToolError("file_unsupported")
    with directory(path.parent) as parent:
        data, version = read(parent, path.name)
    text, truncated, notice = extract(data, path.suffix.casefold())
    return DocumentResult(
        path=str(path), version=version, text=text, truncated=truncated, notice=notice
    )
