"""Regression coverage keeping SECURITY.md's Supported Versions table in
sync with the project's actual version (#6065).

The table used to be hand-maintained prose, so it silently fell behind the
version in ``pyproject.toml`` release after release. These tests derive the
expected minor line from ``pyproject.toml`` itself rather than hardcoding a
version, so a release bump that forgets to touch ``SECURITY.md`` fails here
instead of shipping a stale policy.

Policy: SECURITY.md states fixes ship on the current release line and there
is no backport branch, so exactly one minor line - the current one - is
supported.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
SECURITY_MD = REPO_ROOT / "SECURITY.md"

_ROW_RE = re.compile(r"^\|\s*(?P<version>[^|]+?)\s*\|\s*(?P<supported>[^|]+?)\s*\|\s*$", re.MULTILINE)


def _project_minor_version() -> str:
    """Return ``"<major>.<minor>"`` from the ``version`` declared in pyproject.toml."""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    version = data["project"]["version"]
    major, minor, *_rest = version.split(".")
    return f"{major}.{minor}"


def _supported_versions_table() -> list[tuple[str, str]]:
    """Parse the ``## Supported Versions`` table in SECURITY.md into (version, supported) rows.

    Skips the header row and the ``---`` separator row.
    """
    text = SECURITY_MD.read_text(encoding="utf-8")
    heading = "## Supported Versions"
    start = text.index(heading)
    section = text[start + len(heading) :]
    next_heading = re.search(r"^## ", section, re.MULTILINE)
    if next_heading is not None:
        section = section[: next_heading.start()]

    rows: list[tuple[str, str]] = []
    for match in _ROW_RE.finditer(section):
        version, supported = match.group("version"), match.group("supported")
        if version.lower() == "version" or set(version) <= {"-"}:
            continue
        rows.append((version, supported))
    return rows


def test_pyproject_version_is_parseable() -> None:
    """Sanity check for the fixture the other tests depend on."""
    minor = _project_minor_version()
    assert re.fullmatch(r"\d+\.\d+", minor)


def test_supported_versions_table_has_current_minor_marked_yes() -> None:
    minor = _project_minor_version()
    rows = _supported_versions_table()
    expected_row = (f"{minor}.x", "Yes")
    assert expected_row in rows, (
        f"SECURITY.md's Supported Versions table does not mark the current "
        f"release line ({minor}.x) as supported; got {rows}. "
        f"pyproject.toml declares version {minor}.x -- update the table."
    )


def test_supported_versions_table_marks_everything_else_unsupported() -> None:
    """Current-release-only policy: exactly one row is Yes, and it is the
    current minor. Every other row must be No.

    This is what makes the test catch a *stale* Yes line, not just a missing
    one -- a table that still says the previous minor is supported alongside
    the new one would pass a "current minor is present" check but violates
    the project's stated no-backport-branch policy.
    """
    minor = _project_minor_version()
    rows = _supported_versions_table()
    assert rows, "SECURITY.md's Supported Versions table has no rows to check."

    yes_rows = [version for version, supported in rows if supported == "Yes"]
    assert yes_rows == [f"{minor}.x"], (
        f"Exactly one release line should be marked Yes (the current one, "
        f"{minor}.x) per the no-backport-branch policy; got {yes_rows}."
    )

    for version, supported in rows:
        if version != f"{minor}.x":
            assert supported == "No", f"{version} should be marked No (no backport branch), got {supported!r}."


def test_no_backport_branch_policy_still_documented() -> None:
    """Pins the sentence the version-only-support policy is derived from, so
    a future edit that softens or removes it is a visible diff, not a silent
    policy change."""
    text = SECURITY_MD.read_text(encoding="utf-8")
    assert "Fixes ship on the current release line. There is no backport branch." in text
