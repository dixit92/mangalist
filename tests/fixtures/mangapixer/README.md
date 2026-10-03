# MangaPixer export fixture

`metadata-export-v1.json` is a verbatim copy of MangaPixer's synthetic contract sample
`contracts/samples/metadata-export-v1.json` (MangaPixer repository, branch `dev`, file last changed in
commit `12fa4a5`, copied 2026-10-03 from `dev` @ `573a997`). MangaPixer generates it from a test, so it
matches what MangaPixer 1.33.0 sends for `GET /api/v1/export/metadata` (`schemaVersion` 1): every link
state, a folder and an archive item, a removal, a `carriedFrom`, a volume list with dates, official links.

All names, ids and links in it are made up. Do not edit it by hand; to refresh it, copy the file again
from MangaPixer and note the new commit here. The tests in `tests/services/mangapixer/` read it.
