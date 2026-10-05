"""
Tripwire against the drift issue #31 exists to close (US-4).

The backend's `is_localhost()` and the frontend's `isLocalhost()` each keep a
literal hostname list, because the frontend bundler cannot import from outside
its own repository (see `shared/auth/README.md`). A test that fails when the two
lists disagree is therefore the only thing holding them together - a comment
asking nicely is not a mechanism.

This file does that by reading `frontend/src/lib/settings.ts` as TEXT rather
than importing it. `frontend/` is a git submodule pointing at another
repository, and `frontend/tsconfig.json`'s `include` lists only paths relative
to `frontend/`, so a TypeScript import could not see it. pytest has no such
restriction: it reads the sibling checkout directly, with no build step and no
change to either repository's toolchain.

The entries under test come from `shared/auth/localhost_hosts.json`, the
artifact both sides' tests read. Edit the artifact and this file's expectations
move with it - which is the point.
"""

import json
import re
from pathlib import Path
from typing import Any, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ARTIFACT_PATH = REPO_ROOT / "shared" / "auth" / "localhost_hosts.json"
FRONTEND_SETTINGS_TS = REPO_ROOT / "frontend" / "src" / "lib" / "settings.ts"

# AC-8: an absent `frontend/` must SKIP WITH A REASON, never pass quietly. A
# silent pass would report "frontend and backend agree" while checking nothing,
# which is the exact failure mode this file exists to prevent.
if not FRONTEND_SETTINGS_TS.is_file():
    pytest.skip(
        f"{FRONTEND_SETTINGS_TS.relative_to(REPO_ROOT).as_posix()} is absent - the "
        "`frontend/` submodule is not checked out, so there is nothing to align "
        "against. Clone with `git clone --recurse-submodules` (or "
        "`git submodule update --init`) for this file to run. Skipping silently "
        "here would hide frontend drift; see issue #31, AC-8.",
        allow_module_level=True,
    )

SOURCE = FRONTEND_SETTINGS_TS.read_text(encoding="utf-8")

with ARTIFACT_PATH.open(encoding="utf-8") as _handle:
    _ARTIFACT: Any = json.load(_handle)

EXACT_HOSTS: List[str] = list(_ARTIFACT["exact"])
SUFFIXES: List[str] = list(_ARTIFACT["suffixes"])

# Backend-only entries the frontend MUST reject, asserted here so the divergence
# is deliberate and visible rather than accidental. Pinned in
# `frontend/src/lib/settings.test.ts` as T43/T44.
BACKEND_ONLY_HOSTS = ["0.0.0.0", "testserver"]

# The one entry removed from the frontend rather than mirrored into the backend.
REMOVED_SUFFIX = ".lovableproject.com"

# Parse the two array literals out of the source instead of substring-matching
# the whole file. A comment that merely NAMES an entry must not read as the
# entry being listed, and an entry listed in a comment must not read as being
# absent from the code.
# Terminate on `];` rather than the first `]`, so a bracketed IPv6 entry (which
# contains one) is reported for what it is instead of silently truncating the
# array and producing a misleading message.
_ARRAY_SOURCE = r"""\b{name}\s*=\s*\[(?P<body>.*?)\]\s*;"""
_ARRAY_SPLIT = re.compile(r"""["'`]([^"'`]*)["'`]""")


def _frontend_array(name: str) -> List[str]:
    """Read `const <name> = [...]` out of the frontend source."""
    match = re.search(_ARRAY_SOURCE.format(name=re.escape(name)), SOURCE)
    if match is None:
        raise AssertionError(
            f"no `{name}` array literal found in {FRONTEND_SETTINGS_TS.name} - the "
            "drift tripwire cannot check what it cannot find, so this is a failure "
            "rather than an empty result"
        )
    return _ARRAY_SPLIT.findall(match.group("body"))


FRONTEND_EXACT = _frontend_array("LOCALHOST_EXACT_HOSTS")
FRONTEND_SUFFIXES = _frontend_array("LOCALHOST_SUFFIXES")


@pytest.mark.unit
class TestTheTripwireItself:
    """
    Guards on the guard.

    If the extraction above silently yielded an empty list, every "is absent"
    assertion below would pass vacuously and the file would report frontend and
    backend agreeing while checking nothing. These assertions make that failure
    mode loud instead.
    """

    def test_frontend_exact_array_was_found(self):
        assert FRONTEND_EXACT, "the frontend's exact-host array did not parse"
        assert len(FRONTEND_EXACT) == len(
            EXACT_HOSTS
        ), f"frontend exact array has {FRONTEND_EXACT}, artifact has {EXACT_HOSTS}"

    def test_frontend_suffix_array_was_found(self):
        assert FRONTEND_SUFFIXES, "the frontend's suffix array did not parse"
        assert len(FRONTEND_SUFFIXES) == len(
            SUFFIXES
        ), f"frontend suffix array has {FRONTEND_SUFFIXES}, artifact has {SUFFIXES}"

    def test_frontend_entries_are_lowercase_and_bracket_free(self):
        """Canonical form, or the two lists stop holding the same tokens."""
        for entry in FRONTEND_EXACT + FRONTEND_SUFFIXES:
            assert entry == entry.lower()
            assert not entry.startswith("[") and not entry.endswith("]")


@pytest.mark.unit
class TestSharedEntriesArePresent:
    """T31: every entry the two sides share appears in the frontend's list."""

    @pytest.mark.parametrize("hostname", EXACT_HOSTS)
    def test_canonical_exact_entry_is_listed(self, hostname: str):
        assert hostname in FRONTEND_EXACT, (
            f"{hostname!r} is missing from the frontend's exact-host list "
            f"({FRONTEND_EXACT})"
        )

    @pytest.mark.parametrize("suffix", SUFFIXES)
    def test_canonical_suffix_is_listed(self, suffix: str):
        assert suffix in FRONTEND_SUFFIXES, (
            f"{suffix!r} is missing from the frontend's suffix list "
            f"({FRONTEND_SUFFIXES})"
        )


@pytest.mark.unit
class TestDivergencesAreDeliberate:
    """The three entries where the two lists are meant to differ."""

    @pytest.mark.parametrize("hostname", BACKEND_ONLY_HOSTS)
    def test_backend_only_host_is_not_listed_in_the_frontend(self, hostname: str):
        """
        `0.0.0.0` (wildcard bind) and `testserver` (TestClient's default Host)
        exist on the backend only; a browser never reports either.
        """
        assert hostname not in FRONTEND_EXACT
        assert hostname not in FRONTEND_SUFFIXES

    def test_removed_suffix_is_not_listed(self):
        """T32 / AC-7: `.lovableproject.com` is out of the frontend's list."""
        assert REMOVED_SUFFIX not in FRONTEND_SUFFIXES
        assert REMOVED_SUFFIX not in FRONTEND_EXACT

    def test_removed_suffix_is_gone_from_the_whole_file(self):
        """
        Not merely unlisted - the source must not carry the entry at all, so a
        comment cannot quietly reintroduce the idea.
        """
        assert "lovableproject" not in SOURCE.lower()


@pytest.mark.unit
class TestFrontendNormalises:
    """
    T33 and AC-12: the frontend normalises before matching, like the backend.

    Case-normalisation is what fixes T41 (`MyHost.LOCAL`), and bracket-stripping
    is what lets the frontend's `[::1]` and the backend's `::1` be the same
    token. Without the strip, the two lists would carry two spellings of IPv6
    loopback and this file could not compare them.
    """

    def test_source_lowercases_the_hostname(self):
        assert re.search(r"\.toLowerCase\(\)", SOURCE)

    def test_source_trims_surrounding_whitespace(self):
        """Parity with the backend: `Host: localhost ` is a legal spelling."""
        assert re.search(r"\.trim\(\)", SOURCE)

    def test_source_strips_a_surrounding_ipv6_bracket_pair(self):
        # Tolerant of quoting, so this does not become a prettier-alignment
        # tripwire.
        assert re.search(r"startsWith\(\s*['\"]\[['\"]", SOURCE)
        assert re.search(r"endsWith\(\s*['\"]\]['\"]", SOURCE)

    def test_source_strips_exactly_one_bracket_pair(self):
        assert re.search(r"slice\(\s*1\s*,\s*-1\s*\)", SOURCE)


@pytest.mark.unit
class TestCrossReferences:
    """Each side must point at the other, so the pairing is discoverable."""

    def test_frontend_source_names_the_shared_artifact(self):
        """
        §6 requires a comment in both source files pointing at the JSON and
        saying the two lists must change together. Making its absence a failure
        means a future reader cannot miss the instruction.
        """
        assert "localhost_hosts.json" in SOURCE

    def test_frontend_source_names_the_backend_list(self):
        assert "backend/core/utils.py" in SOURCE

    def test_backend_source_names_the_frontend_list(self):
        """The same obligation, in the other direction."""
        backend_source = (REPO_ROOT / "backend" / "core" / "utils.py").read_text(
            encoding="utf-8"
        )
        assert "frontend/src/lib/settings.ts" in backend_source
        assert "localhost_hosts.json" in backend_source
