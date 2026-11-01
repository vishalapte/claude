"""Reading archived .eml files: the header, date, body and account helpers.

Ported from delphi's core/eml.py. Stdlib only; reads no path of its own, the caller passes
the mail root.
"""

import html
import re
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from pathlib import Path


_HEAD_CHUNK = 16384
_HEAD_CAP = 1 << 20


def read_head(path: Path) -> bytes:
    """The bytes before the first blank line: the header block, never the body."""
    buf = b""
    with open(path, "rb") as fh:
        while len(buf) < _HEAD_CAP:
            chunk = fh.read(_HEAD_CHUNK)
            if not chunk:
                break
            buf += chunk
            ends = [k for k in (buf.find(b"\r\n\r\n"), buf.find(b"\n\n")) if k >= 0]
            if ends:
                return buf[: min(ends)]
    return buf


def _line_end(low, i):
    """(end of the line at `i`, start of the next line); a line ends at CRLF, CR or LF."""
    lf = low.find(b"\n", i)
    cr = low.find(b"\r", i, lf if lf >= 0 else len(low))  # only up to the LF: an LF file has no CR
    k = cr if cr >= 0 else lf
    if k < 0:
        return len(low), len(low)
    return k, k + 2 if low[k : k + 2] == b"\r\n" else k + 1


def header_values(low, names):
    """Each value of a header in `names` found in lowercased raw bytes, folds joined.

    A header is its name at the start of a line, optional spaces, a colon, and its lines while
    the next one starts with a space or tab (a fold); the line ends are dropped. Read from the
    bytes, with no parse, so a whole file can be screened at the speed of bytes.find.
    """
    for name in names:
        i = low.find(name)
        while i >= 0:
            j = i + len(name)
            at_line_start = i == 0 or low[i - 1] in b"\r\n"
            while at_line_start and j < len(low) and low[j] in b" \t":
                j += 1
            if at_line_start and j < len(low) and low[j] == ord(":"):
                parts, k = [], j + 1
                while True:
                    end, nxt = _line_end(low, k)
                    parts.append(low[k:end])
                    if nxt >= len(low) or low[nxt] not in b" \t":
                        break
                    k = nxt
                yield b"".join(parts)
                i = low.find(name, nxt)
            else:
                i = low.find(name, i + 1)


def decode_hdr(raw):
    """Decode an RFC 2047-encoded header value to a plain string; tolerate junk."""
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw))).strip()
    except Exception:  # pylint: disable=broad-exception-caught
        # Malformed encoded-words raise a grab-bag of errors; never abort the walk.
        return raw.strip()


def msg_date(msg):
    """(sortable epoch, YYYY-MM-DD) for a message's Date, or None on failure."""
    try:
        dt = parsedate_to_datetime(str(msg.get("Date", "")))
        if dt is None:
            return None
        return dt.timestamp(), dt.date().isoformat()
    except Exception:  # pylint: disable=broad-exception-caught
        # parsedate_to_datetime raises varied errors on the many malformed dates.
        return None


def html_to_text(raw):
    """Crudely reduce HTML to text: drop style/script, turn block tags into newlines, strip
    the rest, unescape entities."""
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(?:p|div|tr)>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return html.unescape(s)


def _decode_part(part):
    """Decode one MIME part to text using its charset, errors='replace'."""
    try:
        payload = part.get_payload(decode=True)
    except Exception:  # pylint: disable=broad-exception-caught
        payload = None
    if not isinstance(payload, (bytes, bytearray)):
        content = part.get_payload()
        return content if isinstance(content, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, TypeError):
        return payload.decode("utf-8", errors="replace")


def body_text(msg):
    """The message body as text: prefer text/plain, else strip text/html."""
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
    except Exception:  # pylint: disable=broad-exception-caught
        part = None
    if part is None:
        return ""
    text = _decode_part(part)
    if part.get_content_type() == "text/html":
        text = html_to_text(text)
    return text


def account_dirs(root: Path, account):
    """Account dirs to scan under `root`: the named one, or every dir with archive/.

    Raises FileNotFoundError, naming what is missing, when the named account has no archive
    or the root does not exist; the caller turns it into its own refusal.
    """
    if account:
        d = root / account
        if not (d / "archive").is_dir():
            raise FileNotFoundError(f"no archive under {d}")
        return [d]
    if not root.is_dir():
        raise FileNotFoundError(f"no mail root at {root}")
    return sorted(d for d in root.iterdir() if (d / "archive").is_dir())
