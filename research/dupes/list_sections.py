"""Research lane D (throwaway): list the Duplicates view's two sections from a COPY of the database.

Run in a throwaway container: the library mounted read-only, the database copy writable (it is a scratch copy).
Writes JSON to stdout: {"overlaps": [...], "duplicates": [...]}.
"""
import json
import sys

sys.path.insert(0, "/opt/mangalist/app")
from mangalist.duplicates import find_duplicate_files, find_whole_and_parts  # noqa: E402
from mangalist.store import Store  # noqa: E402

db = Store(sys.argv[1])


def dump(groups):
    return [dict(series_id=g.series_id, folder=g.folder, title=g.title, kind=g.kind, number=g.number,
                 overlap=getattr(g, "overlap", None), note=getattr(g, "note", None), usual=g.usual_group,
                 files=[dict(path=f.path, size=f.size, modified=f.modified, group=f.group) for f in g.files])
            for g in groups]


print(json.dumps({"overlaps": dump(find_whole_and_parts(db)), "duplicates": dump(find_duplicate_files(db))}, indent=1))
