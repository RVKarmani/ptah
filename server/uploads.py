"""Streaming multipart upload: file parts go straight to disk as the bytes arrive (never buffered in memory, limit enforced on the fly)."""
from __future__ import annotations

import re
from pathlib import Path

import anyio
from python_multipart.multipart import MultipartParser, parse_options_header

TEXT_FIELD_MAX = 256 * 1024
ROOM_MAX = 2 * 1024 * 1024


class UploadError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


async def receive_form(request, dest: Path, files: dict[str, tuple[str, int]], max_total: int) -> tuple[dict[str, str], dict[str, Path]]:
    """Parse a multipart request body into (text fields, saved files).

    ``files`` maps a form field name to (file name inside ``dest``, max bytes); other file parts are read and discarded.
    """
    ctype = request.headers.get("content-type", "")
    mt, params = parse_options_header(ctype)
    if mt != b"multipart/form-data" or b"boundary" not in params:
        raise UploadError(415, "Send the form as multipart/form-data.")
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > max_total:
        raise UploadError(413, f"That upload is larger than the {max_total // (1024 * 1024)} MB limit.")
    dest.mkdir(parents=True, exist_ok=True)

    fields: dict[str, bytearray] = {}
    saved: dict[str, Path] = {}
    st: dict = {"hf": b"", "hv": b"", "headers": {}, "name": None, "fh": None, "limit": 0, "written": 0, "text": None, "path": None, "field": None}

    def part_begin() -> None:
        st.update(headers={}, name=None, fh=None, text=None, written=0, path=None, field=None, hf=b"", hv=b"")

    def header_field(data, s, e) -> None:
        st["hf"] += data[s:e]

    def header_value(data, s, e) -> None:
        st["hv"] += data[s:e]

    def header_end() -> None:
        st["headers"][st["hf"].lower()] = st["hv"]
        st["hf"] = st["hv"] = b""

    def headers_finished() -> None:
        _, opts = parse_options_header(st["headers"].get(b"content-disposition", b""))
        name = opts.get(b"name", b"").decode("utf-8", "replace")
        filename = opts.get(b"filename")
        st["field"] = name
        if filename is None:
            st["text"] = fields.setdefault(name, bytearray())
        elif name in files:
            fname, limit = files[name]
            st["path"], st["limit"] = dest / fname, limit
            st["fh"] = open(st["path"], "wb")
        # else: unknown file part: discarded

    def part_data(data, s, e) -> None:
        n = e - s
        if st["text"] is not None:
            if len(st["text"]) + n > TEXT_FIELD_MAX:
                raise UploadError(413, f"The field '{st['field']}' is too long.")
            st["text"] += data[s:e]
        elif st["fh"] is not None:
            st["written"] += n
            if st["written"] > st["limit"]:
                raise UploadError(413, f"'{st['field']}' is larger than its {st['limit'] // (1024 * 1024) or 1} MB limit.")
            st["fh"].write(data[s:e])

    def part_end() -> None:
        if st["fh"] is not None:
            st["fh"].close()
            if st["written"] > 0:
                saved[st["field"]] = st["path"]
            else:
                st["path"].unlink(missing_ok=True)
            st["fh"] = None

    parser = MultipartParser(params[b"boundary"], {
        "on_part_begin": part_begin, "on_header_field": header_field, "on_header_value": header_value, "on_header_end": header_end,
        "on_headers_finished": headers_finished, "on_part_data": part_data, "on_part_end": part_end})
    total = 0
    try:
        async for chunk in request.stream():
            total += len(chunk)
            if total > max_total:
                raise UploadError(413, f"That upload is larger than the {max_total // (1024 * 1024)} MB limit.")
            await anyio.to_thread.run_sync(parser.write, chunk)
        parser.finalize()
    except UploadError:
        _abort(st, dest, files)
        raise
    except Exception as e:  # noqa: BLE001 - malformed multipart
        _abort(st, dest, files)
        raise UploadError(400, "The upload could not be read. Please try again.") from e
    return {k: bytes(v).decode("utf-8", "replace") for k, v in fields.items()}, saved


def _abort(st: dict, dest: Path, files: dict) -> None:
    if st.get("fh"):
        try:
            st["fh"].close()
        except OSError:
            pass
    for fname, _ in files.values():
        (dest / fname).unlink(missing_ok=True)


def safe_ext(filename: str) -> str:
    m = re.search(r"\.([A-Za-z0-9]{1,6})$", filename or "")
    return m.group(1).lower() if m else "bin"
