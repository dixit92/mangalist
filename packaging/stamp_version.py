"""Work out the build version and stamp ``manga_list/_version.py`` (CI and local builds).

Versions are calendar versions ``YEAR.MONTH.N`` (``2026.9.0``, then ``2026.9.1`` or ``2026.10.0``): the
year and month of the release plus a counter that restarts each month. No leading zeros.

- A ``v*`` tag build (``GITHUB_REF=refs/tags/v2026.9.0``) is version ``2026.9.0``. The tag must have
  that form, and ``CHANGELOG.md`` must have a ``## [2026.9.0]`` section - a release is never untracked.
- Anything else is ``0.0.0+<short sha>`` (``STAMP_SHA``, else ``GITHUB_SHA``, else ``git rev-parse``).

Prints ``version=...``, ``numeric=...`` (the dotted-number prefix, for installer and bundle metadata)
and ``basename=MangaList-v<version>`` and appends them to ``$GITHUB_OUTPUT`` when set.

    python packaging/stamp_version.py            # compute and stamp
    python packaging/stamp_version.py --dry-run  # compute only
    python packaging/stamp_version.py --dry-run --release-notes notes.md   # + that version's CHANGELOG section
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "manga_list" / "_version.py"
CHANGELOG = ROOT / "CHANGELOG.md"
CALVER = re.compile(r"^20\d{2}\.(?:[1-9]|1[0-2])\.(?:0|[1-9]\d*)$")


def changelog_section(version: str, text: str | None = None) -> str | None:
    """The body of the ``## [<version>]`` section of CHANGELOG.md, or None when there is none."""
    if text is None:
        text = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.is_file() else ""
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    return m.group(1).strip() if m else None


def _short_sha() -> str:
    # STAMP_SHA: the commit to name the build after. CI sets it to the PR head for pull requests
    # (GITHUB_SHA is then GitHub's temporary merge commit, which exists nowhere on the branch).
    sha = os.environ.get("STAMP_SHA", "") or os.environ.get("GITHUB_SHA", "")
    if not sha:
        try:
            sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                 check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            sha = "unknown"
    return sha[:7]


def compute() -> tuple[str, str]:
    ref = os.environ.get("GITHUB_REF", "")
    if ref.startswith("refs/tags/v"):
        version = ref[len("refs/tags/v"):]
        if not CALVER.match(version):
            raise SystemExit(f"Tag {ref!r} is not a release tag like v2026.9.0 (vYEAR.MONTH.N, no leading zeros)")
        if changelog_section(version) is None:
            raise SystemExit(f"CHANGELOG.md has no '## [{version}]' section - add it before tagging")
    else:
        version = f"0.0.0+{_short_sha()}"
    m = re.match(r"\d+(\.\d+){0,2}", version)
    numeric = m.group(0) if m else "0.0.0"
    numeric = ".".join((numeric.split(".") + ["0", "0"])[:3])
    return version, numeric


def main(argv: list[str]) -> int:
    version, numeric = compute()
    if "--release-notes" in argv:
        out_file = Path(argv[argv.index("--release-notes") + 1])
        out_file.write_text((changelog_section(version) or "") + "\n", encoding="utf-8")
    if "--dry-run" not in argv:
        VERSION_FILE.write_text(
            '"""Version stamped by packaging/stamp_version.py at build time."""\n\n'
            f'__version__ = "{version}"\n', encoding="utf-8")
    lines = [f"version={version}", f"numeric={numeric}", f"basename=MangaList-v{version}"]
    print("\n".join(lines))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
