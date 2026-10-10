"""``parse_name``: run the layers in order and apply the caller's per-series context."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import PurePath
from typing import Iterable, Optional, Tuple, Union

from ._common import DANGLING_CHAPTER, ENDS_IN_UNIT_WORD, real_group, series_qualifier, split_extension
from .layers import parse_bare, parse_fmd2, parse_generic, parse_labelled, parse_release, parse_scheme
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
      no number, and ``<series title> 01`` reads 01 even when the title ends in a number.
    """

    schemes: Tuple[Template, ...] = ()
    kind_hint: Optional[Kind] = None
    series_title: Optional[str] = None

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
    result = _first(name, ctx, file_size)
    if result is None:
        return ParsedName(name=name, notes=("no layer recognised the name",))
    return _finish(_apply_hint(result, ctx.kind_hint))


def _finish(result: ParsedName) -> ParsedName:
    """What every layer's result gets (renamer dry run, 2026-10-10):

    - a placeholder group (``[no group]``, ``(Unknown)``, ``N/A``) is no group;
    - the series' "Season N" / "Part N" is the ``qualifier`` (layer 1 reads it from the scheme itself);
    - a name cut off after a chapter word (``0001 [Vol. 0001 Ch.cbz``: the chapter number is lost) is a chapter
      without a number, flagged ``ambiguous`` - never a volume."""
    changes = {}
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
    return (parse_fmd2(name) or parse_release(name) or parse_labelled(name, ctx.series_title)
            or parse_generic(name, file_size) or parse_bare(name, ctx.series_title))


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
    """:func:`parse_name` for every name with one context."""
    return tuple(parse_name(n, context) for n in names)


__all__ = ["KindHint", "Layer", "ParseContext", "parse_name", "parse_names"]
