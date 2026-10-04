# MangaPixer export fixture

`metadata-export-v1.json` is a verbatim copy of MangaPixer's synthetic contract sample
`contracts/samples/metadata-export-v1.json` (MangaPixer repository, tag `v1.34.0` = `6fcb5b3`, copied 2026-10-04; first copied
2026-10-03 from the 1.33.0 version). MangaPixer generates it from a test, so it matches what MangaPixer 1.34.0 sends for
`GET /api/v1/export/metadata` (`schemaVersion` 1): every link state including `CollectionAbout` (new in 1.34.0), a folder and an
archive item, a removal, a `carriedFrom`, a volume list with dates, official links.

All names, ids and links in it are made up. Do not edit it by hand; to refresh it, copy the file again
from MangaPixer and note the new commit here. The tests in `tests/services/mangapixer/` read it.
