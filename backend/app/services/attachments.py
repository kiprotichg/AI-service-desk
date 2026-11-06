"""Saving, checking and loading uploaded files and photos.

Files are stored under UPLOAD_FOLDER with random names, so user-supplied file
names never touch the filesystem. A file's type is decided from its contents
(magic bytes), not from the name or the browser's claimed type.
"""
import os
import uuid
from dataclasses import dataclass

from flask import current_app

from ..models import Attachment

MAX_FILES_PER_MESSAGE = 3
MAX_FILE_BYTES = 5 * 1024 * 1024        # images and PDFs
MAX_TEXT_FILE_BYTES = 200 * 1024        # text files go into the prompt as text
# Total attachment bytes re-sent to the AI per request (newest files first).
AI_ATTACHMENT_BUDGET = 12 * 1024 * 1024

TEXT_EXTENSIONS = {".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
                   ".json": "application/json", ".log": "text/plain"}
ACCEPTED_EXTENSIONS = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", *TEXT_EXTENSIONS]


class UploadError(ValueError):
    """A problem with an uploaded file that the user should be told about."""


@dataclass
class FilePart:
    """An attachment ready to hand to the AI. data is None when over budget."""
    filename: str
    mime_type: str
    data: bytes | None

    @property
    def is_text(self):
        return self.mime_type in TEXT_EXTENSIONS.values()


def _sniff(head):
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def _detect_type(filename, data):
    """Return the MIME type to store, or raise UploadError."""
    ext = os.path.splitext(filename)[1].lower()
    sniffed = _sniff(data[:16])
    if sniffed:
        if len(data) > MAX_FILE_BYTES:
            raise UploadError(f"{filename} is larger than {MAX_FILE_BYTES // 1024 // 1024} MB")
        return sniffed
    if ext in TEXT_EXTENSIONS:
        if len(data) > MAX_TEXT_FILE_BYTES:
            raise UploadError(f"{filename} is larger than {MAX_TEXT_FILE_BYTES // 1024} KB "
                              "(text files are read in full by the AI)")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise UploadError(f"{filename} isn't UTF-8 text") from None
        return TEXT_EXTENSIONS[ext]
    raise UploadError(f"{filename}: only images (PNG, JPEG, GIF, WebP), PDFs and text files "
                      f"({', '.join(TEXT_EXTENSIONS)}) can be uploaded")


def _folder():
    folder = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(folder, exist_ok=True)
    return folder


def save_uploads(files):
    """Validate every file first, then write them. Returns unsaved Attachment rows."""
    files = [f for f in files if f and f.filename]
    if len(files) > MAX_FILES_PER_MESSAGE:
        raise UploadError(f"You can attach up to {MAX_FILES_PER_MESSAGE} files per message")

    checked = []
    for f in files:
        data = f.read()
        if not data:
            raise UploadError(f"{f.filename} is empty")
        name = os.path.basename(f.filename.replace("\\", "/"))[:255] or "file"
        checked.append((name, _detect_type(name, data), data))

    rows = []
    for name, mime, data in checked:
        stored = uuid.uuid4().hex
        with open(os.path.join(_folder(), stored), "wb") as out:
            out.write(data)
        rows.append(Attachment(filename=name, stored_name=stored, mime_type=mime, size=len(data)))
    return rows


def path_of(attachment):
    return os.path.join(current_app.config["UPLOAD_FOLDER"], attachment.stored_name)


def delete_files(attachments):
    for a in attachments:
        try:
            os.remove(path_of(a))
        except FileNotFoundError:
            pass


def load_parts(attachments, budget):
    """Read attachments for the AI. Returns (parts, bytes_left)."""
    parts = []
    for a in attachments:
        data = None
        if a.size <= budget:
            try:
                with open(path_of(a), "rb") as fh:
                    data = fh.read()
                budget -= a.size
            except FileNotFoundError:
                data = None
        parts.append(FilePart(a.filename, a.mime_type, data))
    return parts, budget


def build_history(messages, budget=AI_ATTACHMENT_BUDGET):
    """Turn stored messages into AI history; newest attachments get the byte budget first."""
    turns = []
    for m in reversed(messages):
        parts, budget = load_parts(m.attachments, budget)
        turns.append({"role": m.role, "content": m.content, "attachments": parts})
    turns.reverse()
    return turns
