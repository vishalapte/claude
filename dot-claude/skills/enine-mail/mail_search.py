"""Search the mail store by header flags or a Gmail-style query, read-only.

Ported from delphi's mail/lib/_search.py. The store itself is the one source: every
<store>/<account>/archive/<domain>/<YYYY-MM>/*.eml, whatever filed it; the caller names the store.

One grammar, Gmail's, builds one term tree (AND / OR / NOT over field predicates), and one
matcher runs it. --query takes from: to: cc: bcc: subject: after: before: older_than:
newer_than: has:attachment filename:, bare words and "phrases" (headers and text body),
-term, parentheses, implicit AND, and `or` / `and` in any case, AND binding tighter; a quoted
"or" is the word. A field flag is its field's query: `--from X` is exactly `from:(X)`, each
term matched in the decoded header, display name and address, and `--after D` is `after:D`.
Separate flags and the query are ANDed. A Gmail operator this search cannot answer (in:,
label:, larger: ...) is refused; any other `word:` is a plain word, such as "Re:".

Every term matches whole words, as Gmail's do: where the term starts or ends with a letter or
digit, the text next to it must not be one. So `oci` does not match "Social", while
`alice@example.com` matches "Alice <alice@example.com>" and `example.org` matches
"x@example.org", since `@`, `.`, `<` and `>` separate words. A term ending in `*` is a
prefix: `art*` matches "Arts". There is no substring form.

Dates are compared as IST calendar dates: `after` keeps the day and later, `before` keeps
days strictly before. Speed comes from three things. Month folders outside the date range,
widened by one month each side because the store files by the sender's local Date, are never
opened. A message is parsed past its header block only when a body or attachment term can
still change the verdict. And an attachment term first screens the file's raw bytes (see
screen_for): has:attachment parses only a file whose Content-Type or Content-Disposition
can make an attachment, and filename:TERM only one whose name value can hold the term.

    mail.py search --from "alice or bob" --after 2026-08-01
    mail.py search --query 'from:example.org subject:(dues OR invoice)'
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.message import Message
from email.parser import BytesParser, HeaderParser
from pathlib import Path
from typing import Union

import mail_store
from mail_eml import account_dirs, body_text, decode_hdr, header_values, msg_date, read_head
from mail_store import Refusal

#: Every date a search compares is an Indian Standard Time calendar date.
IST = timezone(timedelta(hours=5, minutes=30), "IST")

#: The headers a field flag, a field operator or a bare word matches.
HEADER_FIELDS = ("from", "to", "cc", "bcc", "subject")

#: The ``--query`` operators; anything else before a colon is refused.
OPERATORS = (
    *HEADER_FIELDS,
    "after",
    "before",
    "older_than",
    "newer_than",
    "has",
    "filename",
)
_STRING_OPERATORS = frozenset((*HEADER_FIELDS, "filename"))

_MONTH_DIR = re.compile(r"^(\d{4})-(\d{2})$")
_DATE = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$")
_RELATIVE = re.compile(r"^(\d+)([dmy])$")
_OPERATOR = re.compile(r"([A-Za-z_]+):")
#: Gmail operators this search cannot answer: refused, so nobody believes they filtered.
#: Any other ``word:`` is a plain word, such as "Re:" in a subject.
UNSUPPORTED = ("in", "label", "is", "category", "larger", "smaller", "size", "list",
               "deliveredto", "rfc822msgid", "around", "older", "newer")


# --------------------------------------------------------------------------- #
# the term tree
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Contains:
    """``value`` (casefolded) occurs as whole words in ``field``: a header, ``text`` or
    ``filename``; a ``value`` ending in ``*`` is a prefix of a word (see ``matches``).

    ``text`` is the five headers and the text body; ``filename`` any attachment name.
    """

    field: str
    value: str


@dataclass(frozen=True)
class HasAttachment:
    """The message carries at least one attached file."""


@dataclass(frozen=True)
class OnOrAfter:
    """The message's IST date is ``day`` or later."""

    day: date


@dataclass(frozen=True)
class Before:
    """The message's IST date is strictly before ``day``."""

    day: date


@dataclass(frozen=True)
class And:
    """Every term holds."""

    terms: tuple[Term, ...]


@dataclass(frozen=True)
class Or:
    """At least one term holds."""

    terms: tuple[Term, ...]


@dataclass(frozen=True)
class Not:
    """The term does not hold."""

    term: Term


Term = Union[Contains, HasAttachment, OnOrAfter, Before, And, Or, Not]


# A letter or digit: the characters a word is made of. Everything else separates words.
_WORD_CHAR = re.compile(r"[^\W_]")


@lru_cache(maxsize=None)
def _pattern(value: str) -> re.Pattern[str]:
    """``value`` as a whole-word pattern; a trailing ``*`` drops the boundary after it."""
    prefix = value.endswith("*")
    core = value[:-1] if prefix else value
    before = "(?<![^\\W_])" if _WORD_CHAR.match(core[:1]) else ""
    after = "(?![^\\W_])" if not prefix and _WORD_CHAR.match(core[-1:]) else ""
    return re.compile(before + re.escape(core) + after)


def matches(value: str, text: str) -> bool:
    """Whether ``value`` occurs in ``text`` as whole words (both casefolded)."""
    return _pattern(value).search(text) is not None


def _lone_star(value: str) -> bool:
    """A term that is only ``*``: a prefix of nothing, which would match every message."""
    return value.endswith("*") and not value[:-1].strip()


def _join(kind: type[And] | type[Or], terms: list[Term]) -> Term:
    return terms[0] if len(terms) == 1 else kind(tuple(terms))


def parse_day(text: str, what: str) -> date:
    """A ``YYYY-MM-DD`` or ``YYYY/MM/DD`` date, or a Refusal naming ``what``."""
    m = _DATE.match(text.strip())
    try:
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        pass
    raise Refusal(f"{what}: {text!r} is not a date; use YYYY-MM-DD or YYYY/MM/DD")


def date_term(name: str, value: str, what: str) -> Term:
    """``after:`` keeps the day and later, ``before:`` the days strictly before."""
    day = parse_day(value, what)
    return OnOrAfter(day) if name == "after" else Before(day)


def _months_back(today: date, months: int) -> date:
    y, m = divmod(today.year * 12 + today.month - 1 - months, 12)
    return date(y, m + 1, min(today.day, calendar.monthrange(y, m + 1)[1]))


def _relative_day(text: str, what: str, today: date) -> date:
    """``today`` less ``Nd``, ``Nm`` (calendar months) or ``Ny``."""
    m = _RELATIVE.match(text.strip())
    if not m:
        raise Refusal(f"{what}{text!r} is not an age; use Nd, Nm or Ny")
    n, unit = int(m.group(1)), m.group(2)
    if unit == "d":
        return today - timedelta(days=n)
    return _months_back(today, n if unit == "m" else 12 * n)


# --------------------------------------------------------------------------- #
# the one grammar: --query, and every field flag read as field:(value)
# --------------------------------------------------------------------------- #
class _QueryParser:
    """A Gmail-style query as a term tree: implicit AND, ``or``/``and`` in any case, ``-``,
    groups, quotes, ``field:``. A field flag's value is parsed with ``parse(field)``, so
    ``--from X`` is exactly ``from:(X)``; ``label`` names the flag in a refusal."""

    def __init__(self, text: str, today: date, label: str = "--query") -> None:
        self.s = text
        self.i = 0
        self.today = today
        self.label = label

    def _fail(self, why: str) -> Refusal:
        return Refusal(f"{self.label}: {why} (at position {self.i} of {self.s!r})")

    def _skip(self) -> None:
        while self.i < len(self.s) and self.s[self.i].isspace():
            self.i += 1

    def _end(self) -> bool:
        self._skip()
        return self.i >= len(self.s)

    def _at_keyword(self, word: str) -> bool:
        self._skip()
        j = self.i + len(word)
        return self.s[self.i : j].lower() == word.lower() and (
            j == len(self.s) or self.s[j].isspace() or self.s[j] in '()"'
        )

    def parse(self, field: str | None = None) -> Term:
        """The whole query as one term, every bare word in ``field`` when given, or a
        Refusal naming where it fails."""
        if self._end():
            raise self._fail("empty query")
        term = self._or(field)
        if not self._end():
            raise self._fail("unbalanced ')'")
        return term

    def _or(self, field: str | None) -> Term:
        terms = [self._and(field)]
        while self._at_keyword("OR"):
            self.i += 2
            terms.append(self._and(field))
        return _join(Or, terms)

    def _and(self, field: str | None) -> Term:
        terms: list[Term] = []
        while not self._end() and self.s[self.i] != ")":
            if self._at_keyword("OR"):
                break
            if self._at_keyword("AND"):
                self.i += 3
                continue
            terms.append(self._unary(field))
        if not terms:
            raise self._fail("expected a term")
        return _join(And, terms)

    def _unary(self, field: str | None) -> Term:
        if self.s[self.i] == "-":
            self.i += 1
            if self.i >= len(self.s) or self.s[self.i].isspace():
                raise self._fail("'-' needs a term after it")
            return Not(self._unary(field))
        return self._primary(field)

    def _group(self, field: str | None) -> Term:
        self.i += 1
        term = self._or(field)
        if self._end() or self.s[self.i] != ")":
            raise self._fail("unbalanced '('")
        self.i += 1
        return term

    def _primary(self, field: str | None) -> Term:
        if self.s[self.i] == "(":
            return self._group(field)
        if self.s[self.i] == '"':
            return self._leaf(field, self._phrase())
        m = _OPERATOR.match(self.s, self.i)
        if m and m.group(1).lower() in UNSUPPORTED:
            raise self._fail(
                f"unknown operator {m.group(1)}:; supported: "
                + " ".join(f"{op}:" for op in OPERATORS)
            )
        if m and m.group(1).lower() in OPERATORS:
            name = m.group(1).lower()
            self.i = m.end()
            if self.i < len(self.s) and self.s[self.i] == "(":
                if name not in _STRING_OPERATORS:
                    raise self._fail(f"{name}: takes one value, not a group")
                return self._group(name)
            if self.i < len(self.s) and self.s[self.i] == '"':
                value = self._phrase()
            else:
                value = self._word()
            if not value:
                raise self._fail(f"{name}: needs a value")
            return self._operator(name, value)
        return self._leaf(field, self._word())

    def _phrase(self) -> str:
        end = self.s.find('"', self.i + 1)
        if end < 0:
            raise self._fail("unbalanced quote")
        value = self.s[self.i + 1 : end]
        self.i = end + 1
        if not value.strip():
            raise self._fail("an empty phrase")
        return value

    def _word(self) -> str:
        j = self.i
        while j < len(self.s) and not self.s[j].isspace() and self.s[j] not in '()"':
            j += 1
        word, self.i = self.s[self.i : j], j
        return word

    def _leaf(self, field: str | None, value: str) -> Term:
        if _lone_star(value):
            raise self._fail("'*' needs a word before it")
        if field is None:
            return Contains("text", value.casefold())
        return self._operator(field, value)

    def _operator(self, name: str, value: str) -> Term:
        if name in _STRING_OPERATORS:
            if _lone_star(value):
                raise self._fail(f"{name}:* needs a word before the '*'")
            return Contains(name, value.casefold())
        if name in ("after", "before"):
            return date_term(name, value, f"{self.label} {name}:")
        if name == "older_than":
            return Before(_relative_day(value, f"{self.label} older_than:", self.today))
        if name == "newer_than":
            return OnOrAfter(_relative_day(value, f"{self.label} newer_than:", self.today))
        if value.lower() != "attachment":  # has:
            raise self._fail(f"has:{value} is not supported; only has:attachment")
        return HasAttachment()


def build_term(
    fields: dict[str, str | None],
    *,
    before: str | None = None,
    after: str | None = None,
    query: str | None = None,
    today: date | None = None,
) -> Term:
    """The flags and the query as one term tree: every part must hold. Each field flag is its
    field's query (``--from X`` is ``from:(X)``) and ``--after D`` is ``after:D``."""
    terms: list[Term] = []
    today = today or datetime.now(IST).date()
    for field in HEADER_FIELDS:
        value = fields.get(field)
        if value is not None:
            terms.append(_QueryParser(value, today, label=f"--{field}").parse(field))
    if after is not None:
        terms.append(date_term("after", after, "--after"))
    if before is not None:
        terms.append(date_term("before", before, "--before"))
    if query is not None:
        terms.append(_QueryParser(query, today).parse())
    if not terms:
        raise Refusal(
            "nothing to search for: give --from, --to, --cc, --bcc, --subject, "
            "--before, --after or --query"
        )
    return _join(And, terms)


def date_bounds(term: Term) -> tuple[date | None, date | None]:
    """(earliest day kept, first day excluded) every match must satisfy, or None.

    Read from the top-level conjuncts only: a date under OR or NOT bounds nothing.
    """
    if isinstance(term, OnOrAfter):
        return term.day, None
    if isinstance(term, Before):
        return None, term.day
    if not isinstance(term, And):
        return None, None
    lo: date | None = None
    hi: date | None = None
    for sub in term.terms:
        sub_lo, sub_hi = date_bounds(sub)
        if sub_lo is not None:
            lo = sub_lo if lo is None else max(lo, sub_lo)
        if sub_hi is not None:
            hi = sub_hi if hi is None else min(hi, sub_hi)
    return lo, hi


# --------------------------------------------------------------------------- #
# the archive walk
# --------------------------------------------------------------------------- #
def _month_number(year: int, month: int) -> int:
    return year * 12 + month - 1


def keep_month(name: str, lo: date | None, hi: date | None) -> bool:
    """Whether a ``YYYY-MM`` folder can hold a match, widened a month each side.

    A folder named otherwise is always kept.
    """
    m = _MONTH_DIR.match(name)
    if not m:
        return True
    month = _month_number(int(m.group(1)), int(m.group(2)))
    if lo is not None and month < _month_number(lo.year, lo.month) - 1:
        return False
    if hi is not None and month > _month_number(hi.year, hi.month) + 1:
        return False
    return True


def _walk(
    accounts: list[Path], lo: date | None, hi: date | None
) -> Iterator[tuple[str, Path]]:
    """(account, path) for every .eml under each account's archive, months pruned."""
    for acct in accounts:
        for domain in sorted((acct / "archive").iterdir()):
            if domain.is_dir():
                for path in _domain_files(domain, lo, hi):
                    yield acct.name, path
            elif domain.suffix == ".eml":
                yield acct.name, domain


def _domain_files(domain: Path, lo: date | None, hi: date | None) -> Iterator[Path]:
    """Every .eml under one counterparty folder, in the months that can match."""
    for month in sorted(domain.iterdir()):
        if month.is_dir():
            if keep_month(month.name, lo, hi):
                yield from sorted(month.rglob("*.eml"))
        elif month.suffix == ".eml":
            yield month


# --------------------------------------------------------------------------- #
# one message, read as far as the verdict needs
# --------------------------------------------------------------------------- #
@dataclass
class _Message:
    """What the matcher reads: decoded headers, the IST date, and when loaded the body."""

    headers: dict[str, str]
    folded: dict[str, str]
    stamp: float | None
    day: date | None
    body: str | None = None
    filenames: list[str] | None = None
    dated: bool = True  # False for a view without the Date header: date terms are undecided

    @property
    def all_headers(self) -> str:
        return "\n".join(self.folded.values())


def _decoded(msg: Message, field: str) -> str:
    values = [str(v) for v in msg.get_all(field, [])]
    return " ".join(decode_hdr(", ".join(values)).split())


def read_headers(path: Path) -> _Message | None:
    """The message's header view, or None when the file cannot be read or parsed."""
    try:
        head = read_head(path).decode("utf-8", errors="replace")
        msg = HeaderParser(policy=policy.compat32).parsestr(head)
        if not msg.keys():
            return None
        headers = {field: _decoded(msg, field) for field in HEADER_FIELDS}
    except Exception:  # pylint: disable=broad-exception-caught
        # A corrupt or unreadable file must not abort the walk; it is counted.
        return None
    when = msg_date(msg)
    stamp: float | None = None
    day: date | None = None
    if when is not None:
        try:
            day = datetime.fromtimestamp(when[0], IST).date()
            stamp = when[0]
        except (OverflowError, OSError, ValueError):
            pass
    return _Message(
        headers=headers,
        folded={k: v.casefold() for k, v in headers.items()},
        stamp=stamp,
        day=day,
    )


def _attachment_names(msg: Message) -> list[str]:
    names: list[str] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        name = part.get_filename()
        if name or part.get_content_disposition() == "attachment":
            names.append(decode_hdr(str(name)) if name else "")
    return names


# Every attachment name comes from a Content-Type `name` or Content-Disposition `filename`
# parameter (RFC 2231 forms included), and a nameless attachment from a Content-Disposition of
# `attachment`: get_filename() and get_content_disposition() read nothing else. So a file is
# screened by the values of those two headers alone, found in its raw bytes, lowercased, with
# folded lines joined (a fold is a line end followed by a space or tab).
_PARAM = re.compile(rb"name(?:[\s*0-9]|\([^()]*\))*=")  # name=, filename=, name*=, filename*0*=


def _mime_values(low: bytes) -> Iterator[bytes]:
    """Each Content-Type and Content-Disposition header value in a lowercased file, unfolded."""
    return header_values(low, (b"content-type", b"content-disposition"))


def _attaching(value: bytes) -> bool:
    """Whether a header value can make a part an attachment: a name parameter, the
    `attachment` disposition, or an RFC 2047 encoded-word, which the parser decodes and which
    can hide either (a value written wholly as =?utf-8?b?...?=)."""
    return b"=?" in value or b"attachment" in value or _PARAM.search(value) is not None


def _may_name(value: bytes, words: tuple[bytes, ...]) -> bool:
    """Whether a name parameter in a header value can decode to text holding every word.

    A plain name is its raw text less quotes, folds and a comment's tail, so its words are in
    the raw value after the `=`. The value cannot show them, and stays a candidate, when the
    name is encoded (RFC 2047 =?...?=, or RFC 2231 name*= and continuations name*0=), holds a
    backslash escape ("in\\voice" is "invoice"), or holds a non-ASCII byte, whose casefold can
    be ASCII (the ligature "\ufb01" folds to "fi").
    """
    if b"=?" in value:
        return True
    for param in _PARAM.finditer(value):
        rest = value[param.end() :]
        if b"*" in param.group() or b"\\" in rest or not rest.isascii():
            return True
        if all(word in rest for word in words):
            return True
    return False


def _term_words(value: str) -> tuple[bytes, ...] | None:
    """The letter-and-digit words of a filename: term, as ASCII bytes; None when the term is not
    ASCII or has no such word, so the raw bytes cannot narrow it."""
    core = value[:-1] if value.endswith("*") else value
    words = re.findall(r"[^\W_]+", core)
    if not words or not core.isascii():
        return None
    return tuple(word.encode() for word in words)


def _read_lower(path: Path) -> bytes | None:
    try:
        return path.read_bytes().lower()
    except OSError:
        return None  # the full parse counts it as unparseable


def may_attach(path: Path) -> bool:
    """Whether the file can hold an attachment; True when it cannot be read."""
    low = _read_lower(path)
    return low is None or any(_attaching(v) for v in _mime_values(low))


def _attachment_terms(term: Term) -> tuple[bool, list[str]]:
    """(whether has:attachment occurs, the filename: values) anywhere in the term tree."""
    if isinstance(term, (And, Or)):
        found = [_attachment_terms(sub) for sub in term.terms]
        return any(h for h, _ in found), [v for _, values in found for v in values]
    if isinstance(term, Not):
        return _attachment_terms(term.term)
    if isinstance(term, HasAttachment):
        return True, []
    if isinstance(term, Contains) and term.field == "filename":
        return False, [term.value]
    return False, []


def screen_for(term: Term):
    """A raw-bytes screen for the term's attachment parts, or None when it has none.

    The screen returns "none" when the file has no attachment (so filenames is exactly []),
    "nomatch" when it may have some but no name can satisfy any filename: term (so [] gives
    the same verdict, though not the attachment count), else "maybe": parse it. A filename:
    term is narrowed by its words only when the tree holds no has:attachment.
    """
    has_any, values = _attachment_terms(term)
    if not has_any and not values:
        return None
    words = [_term_words(value) for value in values]
    narrow = not has_any and all(w is not None for w in words)

    def screen(path: Path) -> str:
        low = _read_lower(path)
        if low is None:
            return "maybe"
        headers = list(_mime_values(low))
        if not any(_attaching(v) for v in headers):
            return "none"
        if narrow and not any(_may_name(v, w) for v in headers for w in words):
            return "nomatch"
        return "maybe"

    return screen


def load_body(path: Path, view: _Message) -> bool:
    """Parse the whole .eml into ``view``: its text body and attachment names."""
    try:
        with open(path, "rb") as fh:
            msg = BytesParser(policy=policy.default).parse(fh)
        view.body = body_text(msg).casefold()
        view.filenames = _attachment_names(msg)
    except Exception:  # pylint: disable=broad-exception-caught
        # A part that will not decode must not abort the walk; it is counted.
        return False
    return True


# --------------------------------------------------------------------------- #
# the matcher: True, False, or None when the body is needed and not loaded
# --------------------------------------------------------------------------- #
def evaluate(  # pylint: disable=too-many-return-statements
    term: Term, view: _Message
) -> bool | None:
    """The term's verdict on the message, or None when it turns on an unread body."""
    if isinstance(term, And):
        verdict: bool | None = True
        for sub in term.terms:
            v = evaluate(sub, view)
            if v is False:
                return False
            if v is None:
                verdict = None
        return verdict
    if isinstance(term, Or):
        verdict = False
        for sub in term.terms:
            v = evaluate(sub, view)
            if v is True:
                return True
            if v is None:
                verdict = None
        return verdict
    if isinstance(term, Not):
        v = evaluate(term.term, view)
        return None if v is None else not v
    if isinstance(term, (OnOrAfter, Before)) and not view.dated:
        return None
    if isinstance(term, OnOrAfter):
        return view.day is not None and view.day >= term.day
    if isinstance(term, Before):
        return view.day is not None and view.day < term.day
    if isinstance(term, HasAttachment):
        return None if view.filenames is None else bool(view.filenames)
    if term.field == "filename":
        if view.filenames is None:
            return None
        return any(matches(term.value, name.casefold()) for name in view.filenames)
    if term.field == "text":
        if matches(term.value, view.all_headers):
            return True
        return None if view.body is None else matches(term.value, view.body)
    if term.field not in view.folded:
        return None  # a view built from a manifest row holds the subject only
    return matches(term.value, view.folded[term.field])


# --------------------------------------------------------------------------- #
# wicket's manifests: a subject per stored file, so a file can be ruled out unread
# --------------------------------------------------------------------------- #
def _has_subject_term(term: Term) -> bool:
    if isinstance(term, (And, Or)):
        return any(_has_subject_term(sub) for sub in term.terms)
    if isinstance(term, Not):
        return _has_subject_term(term.term)
    return isinstance(term, Contains) and term.field == "subject"


def _subject_index(accounts: list[Path]) -> dict[str, str | None]:
    """{stored file path: the subject its manifest row records, or None when unusable}.

    Only the subject is usable: a row's from and to are bare addresses, so a term that
    would match a display name cannot be ruled out by them; it holds no Cc or Bcc; and its
    date is the mailbox's arrival time, not the Date header. A subject is unusable when the
    row lacks it, when it still holds "=?" (an encoded-word the decoder left as is), or
    when two rows name the same file with different subjects. A file no row names is read.
    """
    index: dict[str, str | None] = {}
    for acct in accounts:
        for row in mail_store.manifest_rows(acct):
            if not row.get("path"):
                continue
            key = str(acct / "archive" / str(row["path"]))
            subject = row.get("subject")
            usable = " ".join(subject.split()) if isinstance(subject, str) and "=?" not in subject else None
            index[key] = usable if index.get(key, usable) == usable else None
    return index


def _manifest_view(subject: str) -> _Message:
    """A view holding only a row's subject, so every other term is undecided (None)."""
    return _Message(headers={"subject": subject}, folded={"subject": subject.casefold()},
                    stamp=None, day=None, dated=False)


def _accounts(account: list[str] | None, store: Path) -> list[Path]:
    try:
        if not account:
            return account_dirs(store, None)
        return [account_dirs(store, name)[0] for name in dict.fromkeys(account)]
    except FileNotFoundError as exc:
        raise Refusal(str(exc)) from exc


def _row(account: str, path: Path, view: _Message):
    if view.filenames is None:
        load_body(path, view)
    when = (
        datetime.fromtimestamp(view.stamp, IST).isoformat(timespec="seconds")
        if view.stamp is not None
        else None
    )
    return {
        "date": when,
        "account": account,
        "from": view.headers["from"],
        "subject": view.headers["subject"],
        "attachments": len(view.filenames or []),
        "path": str(path),
    }


def search(  # pylint: disable=too-many-arguments,too-many-locals
    *,
    store: Path,
    sender: str | None = None,
    to: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
    subject: str | None = None,
    before: str | None = None,
    after: str | None = None,
    query: str | None = None,
    account: list[str] | None = None,
    limit: int = 50,
):
    """Every archived message matching all the given flags and the query, newest first.

    Reads the archive and writes nothing. A file that cannot be read or parsed is
    skipped and counted in ``unparseable``.
    """
    if limit < 1:
        raise Refusal(f"--limit must be at least 1, not {limit}")
    term = build_term(
        {"from": sender, "to": to, "cc": cc, "bcc": bcc, "subject": subject},
        before=before,
        after=after,
        query=query,
    )
    lo, hi = date_bounds(term)
    screen = screen_for(term)
    accounts = _accounts(account, store)
    subjects = _subject_index(accounts) if _has_subject_term(term) else {}
    scanned = unparseable = 0
    hits: list[tuple[str, Path, _Message]] = []
    for acct, path in _walk(accounts, lo, hi):
        scanned += 1
        subject = subjects.get(str(path))
        if subject is not None and evaluate(term, _manifest_view(subject)) is False:
            continue  # the subject its manifest row records rules it out: never read
        view = read_headers(path)
        if view is None:
            unparseable += 1
            continue
        verdict = evaluate(term, view)
        if verdict is None and screen is not None:
            found = screen(path)
            if found != "maybe":
                view.filenames = []  # no name can change the verdict: decided without a full parse
                verdict = evaluate(term, view)
                if found == "nomatch":
                    view.filenames = None  # a shown row still counts its real attachments
        if verdict is None:
            if not load_body(path, view):
                unparseable += 1
                continue
            verdict = bool(evaluate(term, view))
        view.body = None  # the verdict is in; keep memory to the headers
        if verdict:
            hits.append((acct, path, view))
    hits.sort(
        key=lambda hit: (hit[2].stamp is not None, hit[2].stamp or 0.0), reverse=True
    )
    return {
        "scanned": scanned,
        "unparseable": unparseable,
        "matched": len(hits),
        "limit": limit,
        "rows": [_row(acct, path, view) for acct, path, view in hits[:limit]],
    }
