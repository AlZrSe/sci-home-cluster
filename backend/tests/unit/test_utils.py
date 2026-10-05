"""
Unit tests for backend/core/utils.py - the backend's half of the localhost
bypass list.

Issue #31 found that this function and the frontend's `isLocalhost()` had
drifted apart, and that neither had a single test: `is_localhost` is the one
place that decides whether authentication is skipped, and nothing asserted
what it accepted. These tests are the backend half of the pair; the frontend
half is `frontend/src/lib/settings.test.ts` and the drift tripwire is
`backend/tests/unit/test_frontend_alignment.py`.

The canonical entries are parametrized off `shared/auth/localhost_hosts.json`
rather than hand-copied, so editing that artifact is what makes this file see
a change (issue #31, AC-6). The backend-only entries (`0.0.0.0`,
`testserver`) and the negatives are written out literally, because they are
NOT shared entries and asserting them here is what makes the divergence
deliberate rather than accidental.
"""

import json
from pathlib import Path
from typing import Any, List

import pytest

from backend.core.utils import is_localhost

ARTIFACT_PATH = (
    Path(__file__).resolve().parents[3] / "shared" / "auth" / "localhost_hosts.json"
)


def _load_artifact() -> Any:
    """Read the reviewable hostname artifact both sides' tests assert against."""
    with ARTIFACT_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


_ARTIFACT: Any = _load_artifact()
EXACT_HOSTS: List[str] = list(_ARTIFACT["exact"])
SUFFIXES: List[str] = list(_ARTIFACT["suffixes"])


@pytest.mark.unit
class TestArtifactShape:
    """The artifact itself, so a malformed one fails loudly (issue #31, AC-5)."""

    def test_has_exactly_the_two_documented_keys(self):
        assert set(_ARTIFACT) == {"exact", "suffixes"}

    def test_both_keys_are_lists_of_strings(self):
        for key in ("exact", "suffixes"):
            assert isinstance(_ARTIFACT[key], list)
            assert all(isinstance(entry, str) and entry for entry in _ARTIFACT[key])

    def test_records_the_ipv6_loopback_entry(self):
        """AC-5: `::1` lives in `exact`, bracket-free (see the trap below)."""
        assert "::1" in EXACT_HOSTS

    def test_records_the_two_shared_suffixes(self):
        assert ".local" in SUFFIXES
        assert ".lovable.app" in SUFFIXES

    def test_omits_the_removed_suffix(self):
        """`.lovableproject.com` is on neither side's list."""
        assert ".lovableproject.com" not in SUFFIXES
        assert ".lovableproject.com" not in EXACT_HOSTS

    def test_entries_are_lowercase_and_bracket_free(self):
        """Canonical form, or the frontend's tokens stop matching."""
        for entry in EXACT_HOSTS + SUFFIXES:
            assert entry == entry.lower()
            assert not entry.startswith("[") and not entry.endswith("]")

    def test_suffixes_are_dot_anchored(self):
        """A leading dot is what stops a bare suffix matching (see T14)."""
        for suffix in SUFFIXES:
            assert suffix.startswith(".")


@pytest.mark.unit
class TestCanonicalArtifactEntries:
    """
    T17: every entry in the artifact is accepted.

    The list under test comes from the JSON, so a new shared entry is a
    one-line artifact edit rather than an edit to every assertion here.
    """

    @pytest.mark.parametrize("hostname", EXACT_HOSTS)
    def test_canonical_exact_entry_is_accepted(self, hostname: str):
        assert is_localhost(hostname) is True

    @pytest.mark.parametrize("hostname", [f"probe{suffix}" for suffix in SUFFIXES])
    def test_canonical_suffix_subdomain_is_accepted(self, hostname: str):
        assert is_localhost(hostname) is True

    @pytest.mark.parametrize("suffix", SUFFIXES)
    def test_canonical_bare_suffix_is_accepted(self, suffix: str):
        """
        The artifact lists the suffixes themselves, so they are accepted too:
        matching is a plain `endswith`, and `".local".endswith(".local")` holds.

        Asserted so the semantics are recorded rather than accidental - note it
        is a weaker guarantee than the subdomain form above, which is why no
        test relies on it.
        """
        assert is_localhost(suffix) is True


@pytest.mark.unit
class TestIPv6Loopback:
    """
    The bug this issue was filed for, in both spellings (issue #31, AC-2).

    Browsers serialise an IPv6 host WITH brackets - `new URL("http://[::1]:5173/")`
    yields `hostname === "[::1]"` - while Starlette builds the URL from the raw
    `Host` header and reads it back through `urlparse`, which strips them, so
    `request.url.hostname` is `"::1"`.

    A list holding the literal `"[::1]"` would pass a unit test and leave the
    real request path broken, because `deps.py` never passes a bracketed value.
    That is why BOTH of these are asserted, and why T20/T21 in `test_auth.py`
    assert the 200 end-to-end through the real dependency.
    """

    @pytest.mark.parametrize(
        "hostname",
        ["::1", "[::1]"],
        ids=["canonical-bracket-free", "browser-bracketed"],
    )
    def test_ipv6_loopback_is_accepted_in_both_forms(self, hostname: str):
        assert is_localhost(hostname) is True

    def test_ipv6_loopback_stays_the_canonical_bracket_free_entry(self):
        """The LIST holds `::1`; the bracket form is handled by normalisation."""
        from backend.core.utils import LOCALHOST_EXACT_HOSTS

        assert "::1" in LOCALHOST_EXACT_HOSTS
        assert "[::1]" not in LOCALHOST_EXACT_HOSTS

    def test_ipv6_loopback_survives_normalisation_of_both_forms(self):
        """The bracket form must reduce to the canonical entry, not merely match."""
        assert is_localhost("[::1]") == is_localhost("::1") is True


@pytest.mark.unit
class TestNormalisation:
    """
    AC-1: the input is normalised before matching.

    The frontend normalises identically (see T41/T15), which is what lets the
    two lists hold the same tokens instead of two spellings of each.
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("LOCALHOST", True),
            ("LocalHost", True),
            ("MyHost.LOCAL", True),
            ("[::1]", True),
            ("::1", True),
        ],
    )
    def test_normalises_case_and_ipv6_brackets(self, raw: str, expected: bool):
        assert is_localhost(raw) is expected

    @pytest.mark.parametrize("raw", [" localhost ", "\tlocalhost\n", " [::1] "])
    def test_normalises_surrounding_whitespace(self, raw: str):
        """
        RFC 7230 s3.2.4 allows a sender to append optional whitespace to a
        field value and requires the recipient to ignore it, so `Host: localhost `
        is a legal spelling of `Host: localhost`. T15 lists `"::1 "`.
        """
        assert is_localhost(raw) is True

    def test_normalises_bracket_and_whitespace_in_either_order(self):
        assert is_localhost(" [::1] ") is True

    def test_strips_at_most_one_bracket_pair(self):
        """`[[::1]]` is not a hostname and must not normalise to `::1`."""
        assert is_localhost("[[::1]]") is False

    @pytest.mark.parametrize("raw", ["", "   ", "[]", "[", "]"])
    def test_normalisation_cannot_invent_a_match(self, raw: str):
        assert is_localhost(raw) is False


@pytest.mark.unit
class TestExactMatches:
    """The four exact hostnames the backend accepts, spelled out (AC-3)."""

    def test_t1_localhost(self):
        """T1: the dev default."""
        assert is_localhost("localhost") is True

    def test_t2_ipv4_loopback(self):
        """T2: what a browser sends for `http://127.0.0.1:5173`."""
        assert is_localhost("127.0.0.1") is True

    def test_t7_wildcard_bind_address_is_backend_only(self):
        """
        T7: `0.0.0.0` is the wildcard BIND address, kept for a dev serving on
        `0.0.0.0:8000` whose proxy preserves the original Host. The frontend
        must reject it - `frontend/src/lib/settings.test.ts` T43.
        """
        assert is_localhost("0.0.0.0") is True

    def test_t8_testserver_is_backend_only(self):
        """
        T8: Starlette `TestClient`'s default Host. Load-bearing for the whole
        TestClient-based suite - `backend/tests/conftest.py` builds every
        client on `http://testserver` - so removing it breaks the suite. A
        browser never sends it; the frontend must reject it (T44).
        """
        assert is_localhost("testserver") is True


@pytest.mark.unit
class TestSuffixMatches:
    """The two shared suffixes (AC-3)."""

    @pytest.mark.parametrize("hostname", ["myhost.local", "printer.local"])
    def test_t5_mdns_suffix(self, hostname: str):
        """
        T5: `.local` is reserved for mDNS by RFC 6762 and is not
        ICANN-delegable, so it cannot be minted on the public internet.
        """
        assert is_localhost(hostname) is True

    def test_t6_lovable_suffix(self):
        """T6: `.lovable.app` preview hosts, already accepted by both sides."""
        assert is_localhost("preview.lovable.app") is True


@pytest.mark.unit
class TestRejections:
    """
    The negatives (AC-3), and the CWE-20 regression guards.

    `endswith` with a leading dot and equality on the whole string are what
    keep `notlocal`, `localhost.evil.com` and `evil-localhost.com` out. These
    already held before issue #31; they are pinned so a future "simplification"
    of the matcher cannot quietly drop them.
    """

    @pytest.mark.parametrize(
        "hostname", ["preview.lovableproject.com", "evil.lovableproject.com"]
    )
    def test_t9_removed_suffix_is_rejected(self, hostname: str):
        """
        T9: `.lovableproject.com` was dropped from the frontend rather than
        mirrored here, so both spellings must be rejected.
        """
        assert is_localhost(hostname) is False

    def test_t10_empty_string(self):
        """T10: the `not hostname` guard."""
        assert is_localhost("") is False

    def test_t11_none(self):
        """
        T11: the `not hostname` guard again, for a host Starlette could not
        parse. The signature is `hostname: str`, so this needs the ignore.
        """
        assert is_localhost(None) is False  # type: ignore[arg-type]

    def test_t12_anchored_suffix_rejects_a_lookalike_domain(self):
        """T12: `localhost.evil.com` ends with neither `.local` nor a shared suffix."""
        assert is_localhost("localhost.evil.com") is False

    def test_t13_exact_match_rejects_a_lookalike_domain(self):
        """T13: `evil-localhost.com` is not the `localhost` entry."""
        assert is_localhost("evil-localhost.com") is False

    def test_t14_leading_dot_rejects_a_bare_suffix(self):
        """T14: `notlocal` cannot match `.local` because of the leading dot."""
        assert is_localhost("notlocal") is False

    @pytest.mark.parametrize("hostname", ["127.0.0.1.evil.com", "testserver.evil.com"])
    def test_t16_no_prefix_matching(self, hostname: str):
        """T16: an exact entry is not a prefix."""
        assert is_localhost(hostname) is False

    # ------------------------------------------------------------------
    # SPEC CONFLICT, recorded rather than silently resolved
    # ------------------------------------------------------------------
    # Issue #31's AC-3 says both of the following, which cannot both hold:
    #
    #   * `.lovable.app` keeps returning True (and §3's authoritative table
    #     keeps `preview.lovable.app` on both sides), and
    #   * `evil.lovable.app` returns False.
    #
    # `preview.lovable.app` and `evil.lovable.app` are the same shape - one
    # label plus the shared suffix - so any suffix-based rule MUST give them
    # the same verdict. Satisfying the second would mean enumerating allowed
    # subdomains instead of matching the suffix, which contradicts the
    # `suffixes` design, the JSON artifact's `suffixes` key, §3's table and
    # T6/T39 on both sides.
    #
    # §3 is declared the authoritative table and it does NOT list
    # `evil.lovable.app` among the negatives - it lists `localhost.evil.com`,
    # `evil-localhost.com` and `notlocal`. So this is implemented per §3 and
    # flagged to the PM for confirmation. Do not "fix" it by narrowing the
    # suffix; the sibling test `test_ac3_rejects_the_documented_lookalikes`
    # is the real CWE-20 guard.

    def test_ac3_rejects_the_documented_lookalikes(self):
        """AC-3's negatives that are actually achievable (see the note above)."""
        for hostname in ("localhost.evil.com", "evil-localhost.com", "notlocal"):
            assert is_localhost(hostname) is False, hostname

    def test_ac3_conflict_evil_lovable_app_is_accepted_like_any_subdomain(self):
        """
        `evil.lovable.app` is a subdomain of `lovable.app` and matches
        `.lovable.app` exactly as `preview.lovable.app` does, so it is
        accepted. Pinned here so the behaviour is a recorded decision rather
        than an accident.
        """
        assert is_localhost("evil.lovable.app") is is_localhost("preview.lovable.app")
        assert is_localhost("evil.lovable.app") is True
