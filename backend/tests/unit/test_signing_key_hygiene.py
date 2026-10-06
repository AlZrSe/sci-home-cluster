"""
Unit tests for signing-key hygiene (issue #56).

Two JWT signing keys were committed to this public repository and the live one -
``.shc/secret_key`` - is what a default checkout signs with. These tests are the
guards for the three things that had to change, and each docstring names the
single mutation that turns it red, so a guard cannot quietly stop guarding:

  * the keys are untracked and ignored, at any depth (U1, U2, U4);
  * no tracked file contains a published key (U3, U5);
  * a published key found on disk is replaced loudly, an unknown key is never
    replaced, and a generated key is reported once (U6, U7, U8, U9);
  * the deny-list's *data* is right, not just its shape (U5, U11) - U11
    re-derives both digests from the committed blobs in git history.

**No published key, and no prefix of one of length >= 8, appears anywhere in
this file.** The real values stay in git history where they already are; every
key used below is synthetic, and the rotation mechanism is exercised by
monkeypatching a digest of a synthetic key into ``PUBLISHED_KEY_SHA256`` rather
than by reading the real list. Copying a real key here would re-publish it in
the one place that ships on every clone.
"""

import hashlib
import logging
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

from backend.core import config
from backend.core.config import PUBLISHED_KEY_SHA256, _resolve_secret_key

# Anchored on this file, not the CWD, so these tests are themselves
# CWD-independent (the property they are asserting about). Every git call below
# also passes -C REPO_ROOT for the same reason.
REPO_ROOT = Path(__file__).resolve().parents[3]

# The two paths that were committed. Kept as the literal repository-relative
# strings so the git assertions name exactly what the commits named.
PUBLISHED_KEY_PATHS = (".shc/secret_key", "backend/.shc/secret_key")

# Never any of these in this file: the shipped values are in history, and the
# deny-list is compared by digest, so the mechanism needs no real key at all.
_MIN_LEAKED_PREFIX = 8

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _git(*args: str) -> subprocess.CompletedProcess:
    """Run git against the repository root, never the working directory."""
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _tracked_paths() -> list[str]:
    """Repository-relative paths in the index, NUL-separated for safety."""
    result = _git("ls-files", "-z")
    assert result.returncode == 0, result.stderr
    return [p for p in result.stdout.split("\0") if p]


def _digest_of_stripped_text(value: str) -> str:
    """The digest the runtime comparison computes for a key read off disk."""
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """Point the resolver at a throwaway state directory with no SECRET_KEY."""
    monkeypatch.setenv("SHC_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("SECRET_KEY", raising=False)
    return tmp_path


def _install_published_key(state_dir: Path, key: str) -> Path:
    """
    Write ``key`` to ``<state_dir>/secret_key`` with a CRLF ending.

    The CRLF is deliberate and load-bearing. The blobs in history are 43 bytes
    with no trailing newline, but a checkout with core.autocrlf=true hands the
    reader a trailing "\r\n", and the runtime comparison hashes the *stripped*
    text. A rotation that hashed the raw bytes instead would match nothing here
    and would never fire in production either - silently, with every other
    assertion still green (issue #56, D8).
    """
    path = state_dir / "secret_key"
    path.write_text(key + "\r\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# U1 - the ignore rule covers both locations
# ---------------------------------------------------------------------------


def test_ignore_rule_covers_both_key_locations():
    """
    U1 / AC-2: one ``.shc/`` rule ignores both key paths.

    ``--no-index`` is mandatory, not decoration: plain ``git check-ignore``
    reports a *tracked* path as not ignored, so without it this test silently
    measures "untracked and ignored" and stops measuring the rule the moment
    untracking regresses (which is exactly what U2 has to catch).

    Mutation that makes it red: delete the ``.shc/`` line from .gitignore, or
    add a later ``!.shc/secret_key`` negation - gitignore is last-match-wins and
    check-ignore evaluates the whole set, so this sees it.
    """
    result = _git("check-ignore", "--no-index", "-v", "--", *PUBLISHED_KEY_PATHS)

    assert result.returncode == 0, (
        "neither key path is ignored; exit 1 means no rule matched "
        f"(stderr: {result.stderr!r})"
    )
    lines = result.stdout.strip().splitlines()
    assert len(lines) == len(PUBLISHED_KEY_PATHS), result.stdout

    # Each line is "<source>:<lineno>:<pattern>\t<path>". The pattern is the
    # third colon-separated field; the trailing field is the path git matched.
    patterns = []
    for line, expected_path in zip(lines, PUBLISHED_KEY_PATHS):
        attribution, _, matched = line.partition("\t")
        source, lineno, pattern = attribution.split(":", 2)
        assert matched == expected_path
        assert source.endswith(".gitignore")
        assert lineno.isdigit()
        patterns.append(pattern)

    # Both paths are covered by the *same* rule, not by two incidental ones: a
    # second rule covering one location would still leave the other exposed.
    assert len(set(patterns)) == 1, f"covered by different rules: {patterns}"


# ---------------------------------------------------------------------------
# U2 - neither key file is tracked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", PUBLISHED_KEY_PATHS)
def test_published_key_file_is_not_tracked(path):
    """
    U2 / AC-1: the key files are absent from the index.

    Checked per path rather than as one ``ls-files`` so a regression that
    untracks only one of them cannot be hidden behind an aggregate assertion.

    Mutation that makes it red: ``git add .shc/secret_key`` (or the backend/ one).

    Separate from U1 on purpose, and the reason is U1 staying *green*: under this
    mutation (``git add -f .shc/secret_key``) both this test and U4 go red -
    ``check-ignore --no-index`` reports a tracked-but-ignored path as ignored, so
    U4's "the rule ignores nothing tracked" assertion fails too. That is
    collateral, not a second signal: what matters is that U1 passes, which
    proves the ``.shc/`` rule itself is still correct and the regression is
    about tracking rather than about ignoring.
    """
    result = _git("ls-files", "--", path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"{path} is still tracked"


# ---------------------------------------------------------------------------
# U3 - no tracked file contains a published key
# ---------------------------------------------------------------------------


def test_no_tracked_file_contains_a_published_key():
    """
    U3 / AC-6: neither published key is in the index, under *any* name.

    A content scan rather than a path check, because "the file is gone" and "the
    secret is gone" are different claims: the same key committed as
    ``config/keys/jwt.txt`` satisfies every path-shaped assertion and still lets
    anyone who clones the repository mint a token the cluster accepts.

    Non-regular entries are skipped: the ``frontend`` gitlink is a commit id,
    not a blob, and there is nothing to read.

    Mutation that makes it red: ``git add -f .shc/secret_key``, or committing the
    same value under a different filename or inside any other tracked file.
    """
    offenders: list[str] = []
    for relative in _tracked_paths():
        path = REPO_ROOT / relative
        if not path.is_file():
            continue
        raw = path.read_bytes()
        # Both forms, so a key committed bare or with a trailing newline is
        # caught regardless of how the runtime comparison strips it.
        candidates = {hashlib.sha256(raw).hexdigest()}
        try:
            candidates.add(_digest_of_stripped_text(raw.decode("utf-8")))
        except UnicodeDecodeError:
            pass
        if candidates & PUBLISHED_KEY_SHA256:
            offenders.append(relative)

    assert offenders == [], (
        "tracked files contain a published signing key: "
        f"{offenders}. Untracking a secret does not unpublish it - the blob "
        "stays in history, in every fork and in every mirror."
    )


# ---------------------------------------------------------------------------
# U4 - the new rule ignores nothing that is tracked
# ---------------------------------------------------------------------------


def test_ignore_rule_ignores_nothing_tracked():
    """
    U4 / AC-2 (negative half): no path in the index is ignored.

    The guard against fixing the leak by ignoring too much. ``.shc/`` is precise
    today; the next edit might be ``*key*``, ``/s/`` or ``*.shc``, and any of
    those would be indistinguishable from a correct fix unless something asserts
    the collateral damage.

    Mutation that makes it red: replacing the ``.shc/`` line with an over-broad
    pattern that starts matching a tracked path.

    Batched through --stdin rather than one subprocess per path: it is the same
    query (exit 0 iff *any* listed path is ignored) at 141 invocations instead
    of one.
    """
    tracked = _tracked_paths()
    assert tracked, "no tracked paths - the index read itself is broken"

    # git reads the candidate paths from stdin, so the whole index is one
    # invocation; the answer is identical to asking about each path separately.
    result = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "check-ignore",
            "--no-index",
            "--stdin",
            "-z",
        ],
        input="\0".join(tracked) + "\0",
        capture_output=True,
        text=True,
        check=False,
    )

    ignored = [p for p in result.stdout.split("\0") if p]
    assert ignored == [], (
        f"the ignore rules swallow {len(ignored)} tracked path(s), e.g. "
        f"{ignored[:5]}. An over-broad rule hides real files from `git status`."
    )


# ---------------------------------------------------------------------------
# U5 - the deny-list is real, not a placeholder
# ---------------------------------------------------------------------------


def test_deny_list_is_well_formed():
    """
    U5 / AC-5: exactly two well-formed, distinct, lowercase hex digests.

    Every test in this file would pass against ``frozenset()``: the rotation
    mechanism is proved with a synthetic digest, so nothing else pins the real
    list. This is the assertion that does - a truncated, uppercase or
    bytes-valued entry disables rotation in production while leaving the suite
    green.

    Mutation that makes it red: ``frozenset()``, a truncated digest, an
    uppercase entry, or a ``bytes`` entry.
    """
    assert len(PUBLISHED_KEY_SHA256) == 2, PUBLISHED_KEY_SHA256
    assert len(set(PUBLISHED_KEY_SHA256)) == 2, "duplicate digests"
    for digest in PUBLISHED_KEY_SHA256:
        assert isinstance(digest, str), f"{digest!r} is not a str"
        assert _HEX64.match(digest), f"{digest!r} is not 64 lowercase hex chars"


# ---------------------------------------------------------------------------
# U11 - the deny-list's digests are the digests of the committed keys
# ---------------------------------------------------------------------------


# Where each published key entered history. Both blobs are still resolvable
# because issue #56 chose untracking over rewriting history (spec D1), so the
# deny-list's data is checkable rather than merely checkable-once-by-a-human.
PUBLISHED_KEY_SOURCES = (
    ("2d1a8cb", ".shc/secret_key", "issue #24"),
    ("08f1f27", "backend/.shc/secret_key", "issue #20"),
)


@pytest.mark.parametrize("rev, path, provenance", PUBLISHED_KEY_SOURCES)
def test_deny_list_digest_matches_the_committed_key(rev, path, provenance):
    """
    U11 / AC-5: each digest is the SHA-256 of the key actually committed at
    ``<rev>:<path>`` - re-derived from the blob, not trusted from the constant.

    U5 above pins the *shape* of ``PUBLISHED_KEY_SHA256``, and every other test
    in this file exercises the rotation with a synthetic digest. Typing one
    character into the constant (``...ed1c`` -> ``...ed1d``) therefore leaves the
    whole suite green while disabling rotation for that key permanently and
    silently. #56's spec answered that with "QA re-derives both digests once",
    which is a human check, not a control: it worked and it expires the first
    time it is forgotten (issue #66).

    **No key material enters the repository.** The blob is read into memory,
    hashed and discarded; only a digest is asserted, and a digest cannot sign.

    ``git cat-file blob``, not ``git show``: with ``core.autocrlf=true`` ``git
    show`` writes 45 bytes for a 43-byte blob, and a digest taken over that
    matches nothing - the "Windows landmine" from spec D8. The text is stripped
    before hashing because that is what ``_resolve_secret_key`` hashes at
    runtime; a normalisation mismatch is exactly the bug being caught.

    Mutation that makes it red: change one hex character of either entry in
    ``PUBLISHED_KEY_SHA256``.
    """
    exists = _git("cat-file", "-e", f"{rev}:{path}")
    if exists.returncode != 0:
        # A --depth 1 clone has no such object, and any future history rewrite
        # would remove it. Skipping keeps CI from failing for a reason that has
        # nothing to do with the code under test; the check runs wherever the
        # history is there to run it.
        shallow = _git("rev-parse", "--is-shallow-repository")
        cause = "shallow clone" if shallow.stdout.strip() == "true" else "absent"
        pytest.skip(
            f"{rev}:{path} is unavailable in this clone ({cause}); "
            "cannot re-derive the digest"
        )

    blob = _git("cat-file", "blob", f"{rev}:{path}").stdout
    assert blob, f"{rev}:{path} read as empty"
    digest = _digest_of_stripped_text(blob)

    assert digest in PUBLISHED_KEY_SHA256, (
        f"the key committed at {rev}:{path} ({provenance}) hashes to a digest "
        f"that is not in PUBLISHED_KEY_SHA256, so _resolve_secret_key() would "
        f"never rotate it. Its digest starts {digest[:16]}. The blob is "
        f"{len(blob.encode('utf-8'))} characters long."
    )


# ---------------------------------------------------------------------------
# U6 - a published key on disk is replaced, loudly
# ---------------------------------------------------------------------------


def test_published_key_is_replaced_and_warns(state_dir, monkeypatch, caplog):
    """
    U6 / AC-4, AC-7, AC-9: rotation replaces the key and warns about it once.

    Deny-listed with a *synthetic* digest, because the mechanism is what is
    under test here; whether the two real digests are correct is U5's job and QA
    re-derives them from history independently.

    Mutations that make it red: deleting the digest check; ``not in`` instead of
    ``in``; downgrading the WARNING to an INFO; hashing the raw file bytes
    instead of the stripped text (which the CRLF payload above is there to
    catch); writing the new key without overwriting the file.

    The synthetic key starts "syn3t1c-" rather than the spec's suggested
    "published-", because the rotation warning itself contains the word
    "published" and the AC-9 substring sweep below would trip over it. The value
    is arbitrary either way - only the digest matters to the mechanism.
    """
    key = "syn3t1c-" + "x" * 40
    path = _install_published_key(state_dir, key)
    monkeypatch.setattr(
        config, "PUBLISHED_KEY_SHA256", frozenset({_digest_of_stripped_text(key)})
    )

    with caplog.at_level(logging.DEBUG, logger="backend.core.config"):
        resolved = _resolve_secret_key()

    # A *different* key is returned, and the one on disk is the new one.
    assert resolved != key
    assert path.read_text(encoding="utf-8").strip() == resolved

    if os.name != "nt":
        # chmod is best-effort on Windows (issue #56, out-of-scope item 10).
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    message = warnings[0].getMessage()
    assert str(path) in message, "the warning must name the absolute key path"
    assert "git history" in message
    assert "invalid" in message.lower()

    # AC-9: the log lines are the one place the value could leak by accident.
    for record in caplog.records:
        text = record.getMessage()
        for start in range(len(key) - _MIN_LEAKED_PREFIX + 1):
            prefix = key[start : start + _MIN_LEAKED_PREFIX]
            assert prefix not in text, f"leaked {prefix!r} into a log record"


# ---------------------------------------------------------------------------
# U7 - an unknown key is never rotated, and the steady state is silent
# ---------------------------------------------------------------------------


def test_unrecognised_key_is_not_rotated_and_logs_nothing(
    state_dir, monkeypatch, caplog
):
    """
    U7 / AC-7: an ordinary restart is a silent no-op.

    Two properties in one test because they are the same property from both
    sides: the key is returned unchanged *and* nothing is emitted. A rotation
    that were unconditional would invalidate everyone's tokens on every restart;
    a log line on every read would train operators to ignore the rotation
    WARNING, which is the one line they must not miss.

    Mutations that make it red: rotating unconditionally; logging on every read;
    ``!=`` in the comparison.
    """
    key = "private-" + "y" * 40
    path = _install_published_key(state_dir, key)
    before_bytes = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns

    # A deny-list that does not contain this key: the real one, which provably
    # does not (it has exactly two entries and neither is this value).
    monkeypatch.setattr(config, "PUBLISHED_KEY_SHA256", frozenset({"0" * 64}))

    with caplog.at_level(logging.DEBUG, logger="backend.core.config"):
        resolved = _resolve_secret_key()

    assert resolved == key
    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime, "the file was rewritten"
    assert caplog.records == [], [r.getMessage() for r in caplog.records]


# ---------------------------------------------------------------------------
# U8 - an explicit SECRET_KEY wins and is never rotated
# ---------------------------------------------------------------------------


def test_explicit_secret_key_is_neither_persisted_nor_rotated(
    state_dir, monkeypatch, caplog
):
    """
    U8: ``SECRET_KEY`` from the environment wins, and the deny-list never sees it.

    An operator who sets the key explicitly owns it. It is not persisted, so it
    cannot be one of the committed values even when it happens to be - and if
    the deny-list check were moved ahead of the environment branch, a
    deliberately-configured key would be reported as compromised and ignored,
    which is both wrong and unfixable from the outside.

    Mutation that makes it red: moving the deny-list check above the env-var
    branch.

    (test_api_fixes.py::TestSecretKeyPersists already covers persistence and
    reuse; this covers precedence and the no-rotation guarantee.)
    """
    configured = "operator-chosen-" + "z" * 32
    monkeypatch.setenv("SECRET_KEY", configured)
    # The deliberately pathological case: the operator's own value is denylisted.
    monkeypatch.setattr(
        config,
        "PUBLISHED_KEY_SHA256",
        frozenset({_digest_of_stripped_text(configured)}),
    )

    with caplog.at_level(logging.DEBUG, logger="backend.core.config"):
        resolved = _resolve_secret_key()

    assert resolved == configured
    assert list(state_dir.iterdir()) == [], "an explicit key must not be persisted"
    assert [
        r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
    ] == []


# ---------------------------------------------------------------------------
# U9 - generation is reported once, with the absolute path
# ---------------------------------------------------------------------------


def test_generation_logs_info_with_the_absolute_path(state_dir, caplog):
    """
    U9 / AC-3, AC-7, AC-9: first use logs one INFO naming the absolute file.

    Asserted against the absolute path rather than the basename, because
    ``SHC_STATE_DIR`` may be relative and "which file is my signing key?" is
    exactly the question #34 had to answer for the database.

    Also asserts the second resolve is silent, so the steady state stays quiet
    (the same property as U7, seen from the generation side).

    Mutations that make it red: logging the path without resolving it
    absolutely; logging at WARNING instead of INFO; logging on every read.
    """
    key_path = state_dir / "secret_key"

    with caplog.at_level(logging.INFO, logger="backend.core.config"):
        generated = _resolve_secret_key()

    assert generated
    assert key_path.read_text(encoding="utf-8").strip() == generated

    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(infos) == 1, [r.getMessage() for r in caplog.records]
    message = infos[0].getMessage()
    assert str(key_path) in message
    assert "generated" in message.lower()
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)

    # AC-9: never the value itself.
    for record in caplog.records:
        text = record.getMessage()
        for start in range(len(generated) - _MIN_LEAKED_PREFIX + 1):
            prefix = generated[start : start + _MIN_LEAKED_PREFIX]
            assert prefix not in text, f"leaked {prefix!r} into a log record"

    # Second resolution is the steady state: same key, no new line.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="backend.core.config"):
        again = _resolve_secret_key()

    assert again == generated
    assert caplog.records == [], [r.getMessage() for r in caplog.records]
