"""Layered archive-name parser (MangaList Product Design, section 4). Pure Python: no Qt, no I/O.

Public API
----------

``parse_name(name, context=None, *, file_size=0) -> ParsedName``
    Units of one archive file name. ``context`` is an optional :class:`ParseContext`:
    ``schemes`` (the root's naming template(s), layer 1), ``kind_hint`` (``"volumes"`` / ``"chapters"``,
    the stored per-series answer for bare numbers) and ``series_title`` (the folder's title).
    ``parse_names(names, context)`` maps it over many names.

``ParsedName`` (frozen dataclass)
    ``kind`` (:class:`Kind`: volume / chapter / both / unknown), ``volume`` / ``chapter`` / ``number``
    (:class:`UnitRange` of ``Decimal`` - ``291.999``, ``12.5``, ``3.10`` exactly as written, never float),
    ``is_extra``, ``is_fraction``, ``title`` (chapter title), ``group``, ``index`` (FMD2's numbering index,
    separate from the chapter number), ``series``, ``year``, ``edition``, ``fix``, ``tags``, ``layer``
    (:class:`Layer`, which layer produced it) and ``notes`` (diagnostics). ``legacy_kind`` gives today's
    ``FileHit.kind`` value.

``Template`` / ``compile_template(source)`` / ``register_token(spec)``
    The naming-template engine: ``Template.render(values)`` and the exact parse-back
    ``Template.parse(stem)``. ``FMD2_CHAPTER_SCHEME`` is the owner's default chapter scheme.

Layers (first match wins)
-------------------------

1. **scheme** - the root's own template inverted (exact; only when the caller passes ``schemes``).
2. **fmd2** - ``NNNN [ ... ]`` / ``Title - NNNN [ ... ]``: ``NNNN`` is the index; units come ONLY from the
   bracket head (``Vol. V``, ``Ch. C``, ``Ch. 10-12``); the title after `` - `` and the group in the last
   ``[...]`` are never read for numbers.
3. **release** - ``Title vNN(-MM) (+ chapters) (Year) (Digital|Digital-Compilation) (Group) (fN)`` and the
   older ``Title vol NN`` / ``Title Vol. NN`` / ``Title cNNN (...)`` / ``Title NNN (Year) (Digital)`` forms.
4. **generic** - today's classifier (``classifier.detect_tokens`` + the ``models`` number regexes),
   unchanged: its kind equals ``FileHit.kind`` and its ``.end`` numbers equal today's per-file maxima.
5. **bare** - ``01.cbz`` / ``Title 01.cbz``: kind unknown with ``number`` set, unless ``kind_hint`` decides.

Each layer is also callable on its own: ``parse_scheme``, ``parse_fmd2``, ``parse_release``,
``parse_generic``, ``parse_bare`` (None when the name is not that layer's shape).
"""

from .layers import parse_bare, parse_fmd2, parse_generic, parse_release, parse_scheme
from .model import Kind, Layer, ParsedName, UnitRange, to_decimal
from .parser import KindHint, ParseContext, parse_name, parse_names
from .template import (
    FMD2_CHAPTER_SCHEME,
    FMD2_VOLUME_SCHEME,
    TOKENS,
    Template,
    TemplateError,
    TokenSpec,
    compile_template,
    register_token,
    values_of,
    windows_safe,
)

__all__ = [
    "FMD2_CHAPTER_SCHEME", "FMD2_VOLUME_SCHEME", "Kind", "KindHint", "Layer", "ParseContext", "ParsedName",
    "TOKENS", "Template", "TemplateError", "TokenSpec", "UnitRange", "compile_template", "parse_bare",
    "parse_fmd2", "parse_generic", "parse_name", "parse_names", "parse_release", "parse_scheme",
    "register_token", "to_decimal", "values_of", "windows_safe",
]
