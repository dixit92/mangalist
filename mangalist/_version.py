"""Single source of truth for the package version.

CI overwrites this file with the git tag when building a release, so the value
below is only what local / development builds report. Releases use calendar
versions (YEAR.MONTH.N, see CHANGELOG.md).
"""

__version__ = "0.0.0.dev0"
