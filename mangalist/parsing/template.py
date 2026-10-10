"""A minimal naming-template engine: render a template, and compile it into an exact parse-back.

Grammar (Gap Filling Design, renaming section):

- fixed text is literal (``[ ]`` and ``( )`` included - they are literal in manga names);
- ``%XX`` is a token from :data:`TOKENS` (longest name wins: ``%CT`` before ``%C``); a numeric token takes an
  optional one-digit minimum width (``%C4`` -> ``0012``, ``%V2`` -> ``03``);
- ``{...}`` is an optional group, dropped when a token inside renders empty; groups nest;
- ``{{`` and ``%%`` are a literal ``{`` and ``%``; ``}}`` is a literal ``}`` outside groups, while inside a
  group ``}`` always closes (so ``{a{b}}`` nests, and a group cannot open with a nested group).

The parse-back is exact for names the same template rendered: a width-``n`` number is either exactly ``n``
digits or more digits without a leading zero, text tokens never start or end with a space, and a group
(``%G``) is balanced brackets one level deep (``[Team [X]]``). One ambiguity remains by construction: a
chapter title that itself ends in ``[...]`` with no group after it reads back as the group.

The token table is extensible (:func:`register_token`); phase 2 builds the full naming engine on it. MangaList's
own scheme (owner, 2026-10-10; :mod:`mangalist.naming`) adds ``%CN`` (the chapter number with at least two decimals:
``%CN4`` -> ``0102.00``), ``%CE`` (the end of a chapter range, same form), ``%VE`` (the end of a volume range) and
``%SQ`` (the season / part of the series a volume belongs to: ``Season 2``, ``Part 5``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Union

from .model import ParsedName, UnitRange, plain, to_decimal


class TemplateError(ValueError):
    """A template that cannot be compiled (unknown token, unbalanced braces)."""


# --- Token table ---------------------------------------------------------------------------------

Values = Mapping[str, Any]


@dataclass(frozen=True)
class TokenSpec:
    """One template token.

    ``field`` is the name the parse-back reports the token's text under (tokens sharing a field must
    agree). ``pattern(width)`` is the token's regex WITHOUT capture groups; ``render(values, width)`` its
    text ("" = empty, which drops an enclosing optional group). ``numeric`` fields are compared by value.
    """

    name: str
    field: str
    pattern: Callable[[Optional[int]], str]
    render: Callable[[Values, Optional[int]], str]
    takes_width: bool = False
    numeric: bool = False
    invertible: bool = True


TOKENS: Dict[str, TokenSpec] = {}


def register_token(spec: TokenSpec, *, replace: bool = False) -> None:
    """Add a token to the table (``replace=True`` to redefine one)."""
    if not re.fullmatch(r"[A-Z][A-Z0-9]*", spec.name) or spec.name[-1].isdigit():
        raise TemplateError(f"bad token name {spec.name!r}")
    if spec.name in TOKENS and not replace:
        raise TemplateError(f"token %{spec.name} already defined")
    TOKENS[spec.name] = spec
    _compile_cached.cache_clear()


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def windows_safe(text: str, replacement: str = "_") -> str:
    """Replace the characters Windows forbids in a file name (``<>:"/\\|?*`` and control characters)."""
    return _UNSAFE.sub(replacement, text)


def _padded(width: Optional[int]) -> str:
    """Digits padded to ``width``: exactly ``width`` digits, or more without a leading zero."""
    n = width or 1
    return rf"(?:\d{{{n}}}|[1-9]\d{{{n},}})"


def _pad(n: int, width: Optional[int]) -> str:
    return str(n).zfill(width or 1)


def _get(values: Values, key: str) -> Any:
    v = values.get(key)
    return None if v is None or (isinstance(v, str) and not v.strip()) else v


def _start(v: Any) -> Optional[Decimal]:
    if isinstance(v, UnitRange):
        return v.start
    return to_decimal(v) if v is not None else None


def _render_index(values: Values, width: Optional[int]) -> str:
    v = _get(values, "index")
    return "" if v is None else _pad(int(v), width)


def _render_volume(values: Values, width: Optional[int]) -> str:
    d = _start(_get(values, "volume"))
    if d is None:
        return ""
    whole, _, frac = plain(d).partition(".")
    return _pad(int(whole), width) + (f".{frac}" if frac else "")


def _render_chapter_whole(values: Values, width: Optional[int]) -> str:
    d = _start(_get(values, "chapter"))
    return "" if d is None else _pad(int(plain(d).partition(".")[0]), width)


def _render_chapter_fraction(values: Values, width: Optional[int]) -> str:
    d = _start(_get(values, "chapter"))
    if d is None:
        return ""
    frac = plain(d).partition(".")[2]
    return f".{frac}" if frac else ""


def _end(v: Any) -> Optional[Decimal]:
    """The end of a range value (a :class:`UnitRange` that is a range); None otherwise."""
    return v.end if isinstance(v, UnitRange) and v.is_range else None


def scheme_number(d: Decimal, width: Optional[int], decimals: int = 0) -> str:
    """A unit number as MangaList's scheme writes it: the whole part padded to ``width`` and at least
    ``decimals`` decimals, more only when the number has more (``Decimal("10.5")`` -> ``0010.50`` for
    width 4 / 2 decimals, ``291.999`` -> ``0291.999``, ``102`` -> ``0102.00``). Trailing zeros past
    ``decimals`` are dropped, so ``3.1`` and ``3.10`` both read ``0003.10`` (the same number)."""
    whole, _, frac = plain(d).partition(".")
    frac = frac.rstrip("0").ljust(decimals, "0")
    return _pad(int(whole), width) + (f".{frac}" if frac else "")


def _render_scheme_chapter(values: Values, width: Optional[int]) -> str:
    d = _start(_get(values, "chapter"))
    return "" if d is None else scheme_number(d, width, 2)


def _render_scheme_chapter_end(values: Values, width: Optional[int]) -> str:
    v = _get(values, "chapter_end")
    d = _start(v) if v is not None else _end(_get(values, "chapter"))
    return "" if d is None else scheme_number(d, width, 2)


def _render_volume_end(values: Values, width: Optional[int]) -> str:
    v = _get(values, "volume_end")
    d = _start(v) if v is not None else _end(_get(values, "volume"))
    return "" if d is None else scheme_number(d, width)


def _two_decimals(width: Optional[int]) -> str:
    return _padded(width) + r"\.\d{2,}"


def _text_renderer(key: str) -> Callable[[Values, Optional[int]], str]:
    def render(values: Values, width: Optional[int]) -> str:
        v = _get(values, key)
        return "" if v is None else str(v).strip()
    return render


_TEXT = r"\S(?:.*?\S)??"                                 # no leading / trailing space
_GROUP_TEXT = r"(?:[^\[\]]|\[[^\[\]]*\])+?"              # balanced brackets, one level deep

for _spec in (
    TokenSpec("I", "index", _padded, _render_index, takes_width=True, numeric=True),
    TokenSpec("V", "volume", lambda w: _padded(w) + r"(?:\.\d+)?", _render_volume, takes_width=True, numeric=True),
    TokenSpec("C", "chapter_whole", _padded, _render_chapter_whole, takes_width=True, numeric=True),
    TokenSpec("CF", "chapter_fraction", lambda w: r"(?:\.\d+)?", _render_chapter_fraction),
    # MangaList's own scheme (owner, 2026-10-10): the chapter number with ALWAYS two decimals ("0102.00"), the end of
    # a chapter range, and the end of a volume range.
    TokenSpec("CN", "chapter", _two_decimals, _render_scheme_chapter, takes_width=True, numeric=True),
    TokenSpec("CE", "chapter_end", _two_decimals, _render_scheme_chapter_end, takes_width=True, numeric=True),
    TokenSpec("VE", "volume_end", lambda w: _padded(w) + r"(?:\.\d+)?", _render_volume_end, takes_width=True,
              numeric=True),
    # The season / part of the series a volume belongs to ("Season 2", "Part 5"; renamer dry run, 2026-10-10).
    TokenSpec("SQ", "qualifier", lambda w: r"(?:Season|Part) (?:0|[1-9]\d*)(?:\.\d+)?", _text_renderer("qualifier")),
    TokenSpec("CT", "title", lambda w: _TEXT, _text_renderer("title")),
    TokenSpec("G", "group", lambda w: _GROUP_TEXT, _text_renderer("group")),
    TokenSpec("T", "series", lambda w: _TEXT, _text_renderer("series")),
    TokenSpec("TR", "series_romaji", lambda w: _TEXT, _text_renderer("series_romaji")),
    TokenSpec("TE", "series_english", lambda w: _TEXT, _text_renderer("series_english")),
    TokenSpec("TM", "series_mu", lambda w: _TEXT, _text_renderer("series_mu")),
    TokenSpec("Y", "year", lambda w: r"\d{4}", _text_renderer("year"), numeric=True),
    TokenSpec("ED", "edition", lambda w: r"[^\s()\[\]](?:[^()\[\]]*?[^\s()\[\]])??", _text_renderer("edition")),
    TokenSpec("FX", "fix", lambda w: r"f\d*", _text_renderer("fix")),
    TokenSpec("O", "original", lambda w: r".+", _text_renderer("original"), invertible=False),
):
    TOKENS[_spec.name] = _spec


# --- Template AST ------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Lit:
    text: str


@dataclass(frozen=True)
class _Tok:
    spec: TokenSpec
    width: Optional[int]


@dataclass(frozen=True)
class _Group:
    children: Tuple[Any, ...]


def _lex(source: str) -> Tuple[Any, ...]:
    names = sorted(TOKENS, key=len, reverse=True)
    pos = 0

    def parse_seq(depth: int) -> List[Any]:
        nonlocal pos
        out: List[Any] = []
        lit: List[str] = []

        def flush() -> None:
            if lit:
                out.append(_Lit("".join(lit)))
                lit.clear()

        while pos < len(source):
            ch = source[pos]
            two = source[pos:pos + 2]
            if two in ("{{", "%%") or (two == "}}" and depth == 0):
                lit.append(ch)
                pos += 2
            elif ch == "{":
                flush()
                pos += 1
                out.append(_Group(tuple(parse_seq(depth + 1))))
            elif ch == "}":
                if depth == 0:
                    raise TemplateError(f"unbalanced '}}' at {pos} in {source!r}")
                pos += 1
                flush()
                return out
            elif ch == "%":
                name = next((n for n in names if source.startswith(n, pos + 1)), None)
                if name is None:
                    raise TemplateError(f"unknown token at {pos} in {source!r}")
                spec = TOKENS[name]
                pos += 1 + len(name)
                width = None
                if spec.takes_width and pos < len(source) and source[pos] in "123456789":
                    width = int(source[pos])
                    pos += 1
                flush()
                out.append(_Tok(spec, width))
            else:
                lit.append(ch)
                pos += 1
        if depth:
            raise TemplateError(f"unclosed '{{' in {source!r}")
        flush()
        return out

    return tuple(parse_seq(0))


class Template:
    """A compiled naming template: :meth:`render` builds a name, :meth:`parse` reads one back exactly."""

    def __init__(self, source: str):
        self.source = source
        self._nodes = _lex(source)
        self._groups: List[TokenSpec] = []
        self._regex = re.compile(self._to_regex(self._nodes), re.DOTALL)
        self.fields = frozenset(s.field for s in self._groups)

    def __repr__(self) -> str:
        return f"Template({self.source!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Template) and other.source == self.source

    def __hash__(self) -> int:
        return hash(self.source)

    @property
    def invertible(self) -> bool:
        """The template can be parsed back: it has tokens and none of them is a pass-through (``%O``)."""
        return bool(self._groups) and all(s.invertible for s in self._groups)

    def _to_regex(self, nodes: Tuple[Any, ...]) -> str:
        parts: List[str] = []
        for node in nodes:
            if isinstance(node, _Lit):
                parts.append(re.escape(node.text))
            elif isinstance(node, _Tok):
                parts.append(f"(?P<t{len(self._groups)}>{node.spec.pattern(node.width)})")
                self._groups.append(node.spec)
            else:
                parts.append(f"(?:{self._to_regex(node.children)})?")
        return "".join(parts)

    # -- parse-back -----------------------------------------------------------------------------

    def parse(self, stem: str) -> Optional[Dict[str, str]]:
        """The token texts of a name stem (no extension) this template produced, by field; None when
        the stem does not fit. A field that occurs twice must carry the same value."""
        m = self._regex.fullmatch(stem)
        if m is None:
            return None
        found: Dict[str, str] = {}
        for i, spec in enumerate(self._groups):
            text = m.group(f"t{i}")
            if text is None or text == "":
                continue
            prev = found.get(spec.field)
            if prev is not None:
                same = (to_decimal(prev) == to_decimal(text)) if spec.numeric else prev == text
                if not same:
                    return None
                continue
            found[spec.field] = text
        return found

    # -- render ---------------------------------------------------------------------------------

    def render(self, values: Union[Values, ParsedName],
               sanitize: Optional[Callable[[str], str]] = windows_safe) -> str:
        """The name stem for ``values`` (keys: index, volume, chapter, title, group, series,
        series_romaji, series_english, series_mu, year, edition, fix, original - or a ParsedName).
        Text tokens pass through ``sanitize`` (Windows-safe by default; None keeps them as given)."""
        vals = values_of(values) if isinstance(values, ParsedName) else values
        text, _ = self._render(self._nodes, vals, sanitize)
        return text

    def _render(self, nodes: Tuple[Any, ...], values: Values,
                sanitize: Optional[Callable[[str], str]]) -> Tuple[str, bool]:
        out: List[str] = []
        complete = True
        for node in nodes:
            if isinstance(node, _Lit):
                out.append(node.text)
            elif isinstance(node, _Tok):
                t = node.spec.render(values, node.width)
                if t == "":
                    complete = False
                elif sanitize is not None and not node.spec.numeric:
                    t = sanitize(t)
                out.append(t)
            else:
                t, ok = self._render(node.children, values, sanitize)
                if ok:
                    out.append(t)
        return "".join(out), complete


def values_of(parsed: ParsedName) -> Dict[str, Any]:
    """Render values from a parse result (for round trips and renames)."""
    return {
        "index": parsed.index, "volume": parsed.volume, "chapter": parsed.chapter, "title": parsed.title,
        "group": parsed.group, "series": parsed.series, "year": parsed.year, "edition": parsed.edition,
        "fix": parsed.fix, "qualifier": parsed.qualifier,
    }


@lru_cache(maxsize=64)
def _compile_cached(source: str) -> Template:
    return Template(source)


def compile_template(template: Union[str, Template]) -> Template:
    """A compiled template (cached by source text)."""
    return template if isinstance(template, Template) else _compile_cached(template)


# MangaList's own scheme (owner, 2026-10-10; Next Cycle Design sections 10-12) - the default of every root whose
# naming scheme is empty, and always read back by the parser (layer 1). Chapters: "Ch. 0102.00 Vol. 012 (<chapter
# title>) [<group>]" - the number first, so a plain sort is the reading order and a volume learned later never moves
# a file; each later part only when known; a range "Ch. 0010.00-0012.00". Volumes: "<Series title> - Vol. 001
# [<group>]", a range "Vol. 001-003", a season / part of the series "<Series title> Season 2 - Vol. 001". mangalist.naming renders them (sanitising, length rule).
MANGALIST_CHAPTER_SCHEME = "Ch. %CN4{-%CE4}{ Vol. %V3}{ (%CT)}{ [%G]}"
MANGALIST_VOLUME_SCHEME = "%T{ %SQ} - Vol. %V3{-%VE3}{ [%G]}"

# The owner's FMD2 scheme (PD 6; MangaList's default until 2026-10-10, now only what FMD2 writes). %I = FMD2's
# numbering index (C1 open for phase 2).
FMD2_CHAPTER_SCHEME = "%I4 [{Vol. %V4 }Ch. %C4%CF{ - %CT}{ [%G]}]"
# Volumes keep their release name.
FMD2_VOLUME_SCHEME = "%O"
