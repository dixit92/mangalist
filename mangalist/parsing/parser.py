"""``parse_name``: run the layers in order and apply the caller's per-series context."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import PurePath
from typing import Iterable, Optional, Tuple, Union

from ._common import DANGLING_CHAPTER, ENDS_IN_UNIT_WORD, real_group, series_qualifier, split_extension, without_watermark
from .heads import SeriesHead, padded_sub_number, series_head_of
from .layers import (
    parse_bare, parse_comics_extra, parse_fmd2, parse_generic, parse_labelled, parse_release, parse_scheme,
)
from .model import Kind, Layer, ParsedName
from .template import MANGALIST_CHAPTER_SCHEME, MANGALIST_VOLUME_SCHEME, Template, compile_template

KindHint = Union[Kind, str, None]

_HINTS = {
    "volume": Kind.VOLUME, "volumes": Kind.VOLUME,
    "chapter": Kind.CHAPTER, "chapters": Kind.CHAPTER,
}


def _kind_hint(hint: KindHint) -> Optional[Kind]:
    if hint is None or hint == "":
        return None
    if isinstance(hint, Kind):
        if hint in (Kind.VOLUME, Kind.CHAPTER):
            return hint
    elif isinstance(hint, str) and hint.strip().lower() in _HINTS:
        return _HINTS[hint.strip().lower()]
    raise ValueError(f"kind hint must be 'volumes' or 'chapters', not {hint!r}")


@dataclass(frozen=True)
class ParseContext:
    """Per-series context for :func:`parse_name` (all optional).

    - ``schemes``: the root's naming scheme(s) - template strings or :class:`Template` - tried first
      (layer 1); a non-invertible scheme such as ``%O`` is skipped.
    - ``kind_hint``: the stored per-series answer to "volumes or chapters?" (``"volumes"`` /
      ``"chapters"``); it decides only names whose kind is unknown from the name itself (bare numbers).
      Asking the owner and storing the answer are the caller's job.
    - ``series_title``: the series folder's title; a bare name that equals it (``42.cbz`` in ``42``) has
      no number, ``<series title> 01`` reads 01 even when the title ends in a number, and a number the title ends
      with is never read as a unit (``<Title 100> v01`` in the folder ``<Title 100>`` is volume 1, not chapter 100 -
      MangaPixer 1.42.0 hand-over, 2026-10-12).
    - ``series_head``: the folder's :class:`~mangalist.parsing.heads.SeriesHead`
      (:func:`~mangalist.parsing.heads.series_head_of` over the folder's names; :func:`parse_names` works it out when
      it is not given): a number right before a volume token, after that head, is the chapter only when it varies
      between the names - a number they all share is part of the title (Mihon's rule).
    """

    schemes: Tuple[Template, ...] = ()
    kind_hint: Optional[Kind] = None
    series_title: Optional[str] = None
    series_head: Optional[SeriesHead] = None

    def __post_init__(self) -> None:
        schemes = self.schemes
        if isinstance(schemes, (str, Template)):
            schemes = (schemes,)
        object.__setattr__(self, "schemes", tuple(compile_template(s) for s in (schemes or ())))
        object.__setattr__(self, "kind_hint", _kind_hint(self.kind_hint))


_NO_CONTEXT = ParseContext()

# MangaList's own scheme is read back exactly in every root, after the root's own scheme (if any): every name the
# renamer writes keeps its title and group as free text ("Ch. 0010.00 (The Vol 2 Begins) [G]" is chapter 10, no volume).
_BUILT_IN_SCHEMES: Tuple[Template, ...] = (compile_template(MANGALIST_CHAPTER_SCHEME),
                                           compile_template(MANGALIST_VOLUME_SCHEME))


def parse_name(name: Union[str, "os.PathLike[str]"], context: Optional[ParseContext] = None, *,
               file_size: int = 0) -> ParsedName:
    """Units of one archive file name (e.g. ``"0012 [Vol. 3 Ch. 12.5 - Title [Group]].cbz"``).

    ``name`` is the file name only (a path-like object contributes its last component; a string is
    taken as is, so a backslash or colon inside a name from a Linux share stays part of the name).
    ``file_size`` (bytes) feeds the generic layer exactly as today's classifier uses it. Never raises
    on any name; a name no layer recognises yields kind UNKNOWN, layer NONE.
    """
    if isinstance(name, PurePath):
        name = name.name
    elif not isinstance(name, str):
        name = os.path.basename(os.fspath(name))
    ctx = context or _NO_CONTEXT
    read = without_watermark(name)
    result = _first(read, ctx, file_size)
    if result is None:
        return ParsedName(name=name, notes=("no layer recognised the name",))
    if read != name:
        result = replace(result, name=name, notes=result.notes + ("a site's watermark left out of the name",))
    return _finish(_apply_hint(result, ctx.kind_hint))


# A sub-number padded to three digits after the point, like the chapter number before it ("Ch.014.005" - owner,
# 2026-10-11: "It is .5"): the three digits are the sub-number, so 014.005 is 14.5 and 021.010 is 21.10 (= 21.1).
_sub = padded_sub_number


def _unpadded(unit):
    if unit is None:
        return None
    start, end = _sub(unit.start), _sub(unit.end)
    return unit if (start, end) == (unit.start, unit.end) else replace(unit, start=start, end=end)


def _finish(result: ParsedName) -> ParsedName:
    """What every layer's result gets (renamer dry run, 2026-10-10):

    - a placeholder group (``[no group]``, ``(Unknown)``, ``N/A``) is no group;
    - the series' "Season N" / "Part N" is the ``qualifier`` (layer 1 reads it from the scheme itself);
    - a name cut off after a chapter word (``0001 [Vol. 0001 Ch.cbz``: the chapter number is lost) is a chapter
      without a number, flagged ``ambiguous`` - never a volume."""
    changes = {}
    chapter = _unpadded(result.chapter)
    if chapter != result.chapter:
        changes["chapter"] = chapter
    group = real_group(result.group)
    if group != result.group:
        changes["group"] = group
    if result.qualifier is None and result.layer is not Layer.SCHEME:
        qualifier = series_qualifier(result.series)
        if qualifier is not None:
            changes["qualifier"] = qualifier
    if result.chapter is None and (result.volume is not None or result.number is not None) \
            and DANGLING_CHAPTER.search(split_extension(result.name)[0]):
        changes.update(kind=Kind.CHAPTER, number=None, guessed=False, ambiguous=True, is_extra=False,
                       notes=result.notes + ("cut off after a chapter word: the chapter number is lost",))
    return replace(result, **changes) if changes else result


def _first(name: str, ctx: ParseContext, file_size: int) -> Optional[ParsedName]:
    for scheme in ctx.schemes + tuple(s for s in _BUILT_IN_SCHEMES if s not in ctx.schemes):
        r = parse_scheme(name, scheme)
        if r is not None:
            return r
    return (parse_fmd2(name) or parse_comics_extra(name) or parse_release(name, ctx.series_title, ctx.series_head)
            or parse_labelled(name, ctx.series_title, ctx.series_head) or parse_generic(name, file_size)
            or parse_bare(name, ctx.series_title))


def _apply_hint(result: ParsedName, hint: Optional[Kind]) -> ParsedName:
    """The series' answer decides a bare number; without one, a bare number AFTER the series title is read as a
    chapter by guess (``guessed``: Next Cycle Design section 10, owner's library 2026-10-10 - of 830 unreadable files
    ~471 were such chapters: ``<Title>. 035 (2023) (Digital)``, ``<Title> 014.6  <chapter title>``, ``<Title> 07``).
    A name that is only a number (``01.cbz``) stays unknown until the owner answers (C12). The guess keeps the
    question open: the series still asks "volumes or chapters?", and the renamer leaves guessed files alone."""
    if result.kind is not Kind.UNKNOWN or result.number is None:
        return result
    if hint is Kind.VOLUME:
        return replace(result, kind=Kind.VOLUME, volume=result.number,
                       notes=result.notes + ("kind from the series hint: volumes",))
    if hint is Kind.CHAPTER:
        return replace(result, kind=Kind.CHAPTER, chapter=result.number,
                       notes=result.notes + ("kind from the series hint: chapters",))
    if result.series and result.layer in (Layer.BARE, Layer.RELEASE) and not ENDS_IN_UNIT_WORD.search(result.series):
        return replace(result, kind=Kind.CHAPTER, chapter=result.number, guessed=True,
                       notes=result.notes + ("a bare number after the series title: a chapter (guess)",))
    return result


def parse_names(names: Iterable[Union[str, "os.PathLike[str]"]],
                context: Optional[ParseContext] = None) -> Tuple[ParsedName, ...]:
    """:func:`parse_name` for every name of one folder with one context. Without a ``series_head`` in the context, the
    names' own :func:`~mangalist.parsing.heads.series_head_of` is used (the folder's siblings tell a chapter number
    from a number of the title)."""
    names = [n.name if isinstance(n, PurePath) else n if isinstance(n, str) else os.path.basename(os.fspath(n))
             for n in names]
    ctx = context or _NO_CONTEXT
    if ctx.series_head is None:
        head = series_head_of(names)
        if head is not None:
            ctx = replace(ctx, series_head=head)
    return tuple(parse_name(n, ctx) for n in names)


def with_series_head(context: Optional[ParseContext], names: Iterable[str]) -> Optional[ParseContext]:
    """``context`` with the series head of a folder's archive ``names`` (unchanged when it has one, or the names give
    none) - for callers that parse a folder's names one by one."""
    if context is not None and context.series_head is not None:
        return context
    head = series_head_of(names)
    if head is None:
        return context
    return replace(context or _NO_CONTEXT, series_head=head)


__all__ = ["KindHint", "Layer", "ParseContext", "parse_name", "parse_names", "with_series_head"]
