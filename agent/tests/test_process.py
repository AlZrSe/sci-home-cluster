"""
The platform-dependent half, tested on whichever half this host is.

Every test here is written so that it runs on **both** platforms and skips
only where the coverage is genuinely the other platform's. That rule is not
tidiness: a Windows-only feature that is ``if sys.platform == "win32":``-ed
out on Linux is exercised nowhere at all, which is how the code that only ever
worked on POSIX reached the agent in the first place (issue #125 Q8c).
"""

from __future__ import annotations

import ast
import asyncio
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, cast

import psutil
import pytest

from agent.config import AgentSettings
from agent.process import (
    ACCEPTED_SHELLS,
    CONTROL_MODE_GROUP,
    CONTROL_MODE_JOB,
    CONTROL_MODE_LEADER,
    CONTROL_MODE_TASKKILL,
    CREATE_NEW_PROCESS_GROUP,
    STOP_GRACE_S,
    STOP_VERIFY_POLL_S,
    STOP_VERIFY_TIMEOUT_S,
    JobFactory,
    JobHandle,
    ProcessTree,
    ShellSpec,
    launch_flags,
    resolve_shell,
    shell_argv,
)
import agent.process as process_module
from agent.tests.conftest import (
    REPO_ROOT,
    Tree,
    build_tree,
    wait_until,
)

pytestmark = pytest.mark.unit

PROCESS_SOURCE = REPO_ROOT / "agent" / "process.py"

#: The reasons a POSIX-only assertion is skipped have to say *why*, because
#: "skipped on Linux" reads as "not applicable" when the honest answer is
#: "we never looked". These are the two facts each one rests on.
NO_POSIX_GROUPS = "no POSIX process groups on Windows"
NO_CONSOLE_CONTROL = "no process groups or console control events on POSIX"
NO_JOB_OBJECTS = "Job Objects are a Windows facility"


def node_settings() -> AgentSettings:
    """Settings carrying only the number AC-15 is about."""
    return AgentSettings(
        NODE_ID="node-01",
        SYNCTHING_ROOT=REPO_ROOT,
        AGENT_STATE_DIR=REPO_ROOT / ".agent" / "node-01",
    )


# --- AC-7: the shell table, asserted as a table ----------------------------


def platform_default() -> tuple[str, tuple[str, ...], bool]:
    if sys.platform == "win32":
        return "cmd", ("/d", "/s", "/c"), True
    return "sh", ("-c",), False


@pytest.mark.parametrize(
    ("configured", "expected_name", "expected_flags", "expected_single"),
    [
        # auto: the platform default, spelled out rather than derived, so a
        # change to it has to be made in the test as well as in the table.
        ("auto", *platform_default()),
        ("AUTO", *platform_default()),
        ("  auto  ", *platform_default()),
        ("sh", "sh", ("-c",), False),
        ("SH", "sh", ("-c",), False),
        ("dash", "dash", ("-c",), False),
        ("bash", "bash", ("-c",), False),
        ("cmd", "cmd", ("/d", "/s", "/c"), True),
        ("pwsh", "pwsh", ("-NoProfile", "-NonInteractive", "-Command"), True),
        (
            "powershell",
            "powershell",
            ("-NoProfile", "-NonInteractive", "-Command"),
            True,
        ),
    ],
)
def test_each_accepted_shell_resolves_to_its_documented_spec(
    configured: str,
    expected_name: str,
    expected_flags: tuple[str, ...],
    expected_single: bool,
) -> None:
    """AC-7: the *resolved program*, the flags and the convention.

    Asserting only that construction succeeded would pass with an empty table,
    which is the failure mode a table test exists to prevent.
    """
    spec = resolve_shell(configured)

    # `.stem`, not `.name`: on a Windows box with Git for Windows installed,
    # which("sh") is an sh.EXE. The configured *shell* is "sh" either way --
    # which is exactly why Git's sh is selectable by name but is not the
    # default (issue #125 Q1).
    assert Path(spec.program).stem.lower() == expected_name
    assert spec.flags == expected_flags
    assert spec.single_string is expected_single


def test_the_flags_are_fixed_per_shell_and_never_taken_from_the_setting() -> None:
    """D4: a configurable flag set is how a quoting contract gets broken."""
    for configured in ACCEPTED_SHELLS:
        spec = resolve_shell(configured)
        assert spec.flags, f"{configured} resolved to a shell with no flags at all"


def test_a_shell_path_is_accepted_and_resolved_where_it_actually_is(
    tmp_path: Path,
) -> None:
    """An absolute path is honoured, and its *basename* picks the convention.

    Two nodes may legitimately have the same shell in different places, so
    the path is what gets resolved -- but whether the command line is assembled
    as one string or as a list is still a property of the program, not of the
    location it happens to sit in.
    """
    fake = tmp_path / "cmd.exe"
    fake.write_text("", encoding="utf-8")

    spec = resolve_shell(str(fake))

    assert spec.program == str(fake)
    assert spec.single_string is True
    assert spec.flags == ("/d", "/s", "/c")


def test_an_unknown_shell_names_every_accepted_value() -> None:
    """AC-7: the rejection message is what an operator on every node reads."""
    with pytest.raises(ValueError) as excinfo:
        resolve_shell("zsh")

    message = str(excinfo.value)
    for accepted in ACCEPTED_SHELLS:
        assert accepted in message
    assert "zsh" in message


def test_a_path_naming_no_known_shell_is_rejected() -> None:
    with pytest.raises(ValueError):
        resolve_shell("/usr/local/bin/fish")


# --- AC-7/D2: how the command line is handed over --------------------------


def test_an_argv_shell_gets_a_list_and_the_command_is_the_last_argument() -> None:
    spec = ShellSpec(program="/bin/sh", flags=("-c",), single_string=False)

    argv = shell_argv(spec, "echo hello && exit 3")

    assert isinstance(argv, list)
    assert argv == ["/bin/sh", "-c", "echo hello && exit 3"]


def test_a_parsing_shell_gets_one_assembled_string() -> None:
    spec = ShellSpec(program="cmd.exe", flags=("/d", "/s", "/c"), single_string=True)

    argv = shell_argv(spec, "a && b")

    assert isinstance(argv, str)
    assert argv == '"cmd.exe" /d /s /c "a && b"'


def test_the_operator_quotes_survive_the_assembly() -> None:
    """§1.3, restated as a test: quoting is the whole reason for the string form.

    ``list2cmdline`` would escape the operator's quotes as ``\\"`` and
    ``cmd.exe`` would read that literally, so every command that quotes
    anything would fail. The assembled string is what the operator would have
    typed at a prompt, character for character.
    """
    spec = ShellSpec(program="cmd.exe", flags=("/d", "/s", "/c"), single_string=True)
    command = '"C:\\Program Files\\Git\\bin\\sh.exe" -c "exit 7"'

    assembled = shell_argv(spec, command)

    assert isinstance(assembled, str)
    assert assembled.endswith(f'"{command}"')
    assert "\\" not in assembled.replace(f'"{command}"', "")


@pytest.mark.skipif(
    sys.platform != "win32",
    reason=NO_POSIX_GROUPS + ": the list form is the one Windows breaks",
)
def test_the_list_form_is_what_breaks_a_quoted_command_on_windows() -> None:
    """The measurement that decided D2, pinned so it cannot be "optimised" back.

    ``cmd.exe`` is invoked with the command both ways; the list form is
    expected to fail and the string form to work. A test that asserted the
    list form *worked* would be asserting the bug.
    """
    import shutil

    cmd = shutil.which("cmd.exe")
    assert cmd is not None
    command = f'"{sys.executable}" -c "import sys; sys.exit(7)"'

    as_list = subprocess.run(
        [cmd, "/d", "/s", "/c", command], capture_output=True, text=True
    )
    as_string = subprocess.run(
        shell_argv(ShellSpec(cmd, ("/d", "/s", "/c"), True), command),
        capture_output=True,
        text=True,
        shell=False,
    )

    assert as_list.returncode != 0
    assert as_string.returncode == 7


# --- launch flags ----------------------------------------------------------


def test_launch_flags_are_the_platforms_own_group_mechanism() -> None:
    flags = launch_flags()

    if sys.platform == "win32":
        assert flags == {"creationflags": CREATE_NEW_PROCESS_GROUP}
        assert flags["creationflags"] == 0x00000200
    else:
        assert flags == {"start_new_session": True}


# --- AC-2: ctypes is confined to the Windows guard -------------------------


def test_ctypes_appears_only_inside_a_windows_platform_guard() -> None:
    """AC-2, as structure rather than as a promise.

    ``grep`` is enough to *look* at this, but grep cannot tell whether the
    reference is reachable by a POSIX type checker. This walks the module with
    the same reachability rule mypy applies to ``sys.platform`` -- both the
    ``if sys.platform == "win32":`` branch form and the
    ``if sys.platform != "win32": raise`` guard form -- and asserts that every
    Windows-only name is in the part a POSIX run never sees.
    """
    tree = ast.parse(PROCESS_SOURCE.read_text(encoding="utf-8"))
    reachable = _reachable(tree, posix=True)

    offenders = [
        f"{name} at line {node.lineno}"
        for node in ast.walk(tree)
        if (name := _windows_only_name(node)) is not None and id(node) not in reachable
    ]

    assert offenders == [], (
        "the POSIX type checker must not see Windows-only API (issue #125 AC-2): "
        f"{offenders}"
    )


def test_posix_only_process_api_is_invisible_to_the_windows_type_checker() -> None:
    """The same rule from the other side, so neither guard can rot.

    ``os.killpg`` and ``signal.SIGKILL`` do not exist on Windows: they are four
    of the five ``mypy .`` errors issue #125 was filed against, guarded by the
    same mechanism for the same reason.
    """
    tree = ast.parse(PROCESS_SOURCE.read_text(encoding="utf-8"))
    reachable = _reachable(tree, posix=False)

    offenders = [
        f"{name} at line {node.lineno}"
        for node in ast.walk(tree)
        if (name := _posix_only_name(node)) is not None and id(node) not in reachable
    ]

    assert (
        offenders == []
    ), f"the Windows type checker must not see POSIX-only API: {offenders}"


def _reachable(tree: ast.Module, *, posix: bool) -> set[int]:
    """Node ids mypy would still analyse on the named platform.

    Two forms matter and both appear in the module: the branch form
    (``if sys.platform == "win32": ...``) and the guard form
    (``if sys.platform != "win32": raise``) which makes the *rest of the
    enclosing body* unreachable on the other platform.
    """
    reachable: set[int] = set()

    def live(flag: bool) -> bool:
        """Whether that per-platform flag means *the platform being asked about*."""
        return flag if posix else not flag

    def note(node: ast.AST, on_posix: bool, on_windows: bool) -> None:
        if live(on_posix) or live(on_windows):
            reachable.add(id(node))

    def walk_body(body: list[ast.stmt], on_posix: bool, on_windows: bool) -> None:
        for statement in body:
            walk(statement, on_posix, on_windows)
            if _always_exits(statement):
                on_posix, on_windows = False, False

    def walk(node: ast.AST, on_posix: bool, on_windows: bool) -> None:
        if isinstance(node, ast.If) and _is_platform_guard(node.test):
            note(node.test, on_posix, on_windows)
            # The branch a platform can never take is dead on that platform,
            # whatever it contains -- which is the whole point of guarding the
            # bodies rather than the names.
            taken, skipped = (
                ((False, on_windows), (on_posix, False))
                if _is_positive(node.test)
                else ((on_posix, False), (False, on_windows))
            )
            walk_body(node.body, *taken)
            walk_body(node.orelse, *skipped)
            return
        note(node, on_posix, on_windows)
        for child in ast.iter_child_nodes(node):
            walk(child, on_posix, on_windows)

    walk_body(tree.body, posix, not posix)
    return reachable


def _always_exits(node: ast.AST) -> bool:
    if isinstance(node, (ast.Raise, ast.Return, ast.Continue, ast.Break)):
        return True
    if isinstance(node, ast.If):
        return _is_platform_guard(node.test) and bool(node.body) and not node.orelse
    return False


def _platform_comparison(test: ast.expr) -> tuple[ast.expr, bool] | None:
    if not isinstance(test, ast.Compare) or not isinstance(test.left, ast.Attribute):
        return None
    left = test.left
    if not (
        isinstance(left.value, ast.Name)
        and left.value.id == "sys"
        and left.attr == "platform"
    ):
        return None
    if not any(
        isinstance(comparator, ast.Constant) and comparator.value == "win32"
        for comparator in test.comparators
    ):
        return None
    return left, isinstance(test.ops[0], ast.Eq)


def _is_platform_guard(test: ast.expr) -> bool:
    """``sys.platform`` compared with ``"win32"``, either way.

    ``!=`` counts as well as ``==``: mypy narrows both, and the production code
    uses the negative form when a Windows-only body has to stay callable (a
    ``raise``/``return`` guard) rather than the positive form inside an
    ``if``.
    """
    return _platform_comparison(test) is not None


def _is_positive(test: ast.expr) -> bool:
    found = _platform_comparison(test)
    assert found is not None
    return found[1]


def _windows_only_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute) and node.attr in ("WinDLL", "CDLL"):
        return f"ctypes.{node.attr}"
    if isinstance(node, ast.Name) and node.id == "wintypes":
        return "wintypes"
    if isinstance(node, ast.ImportFrom) and node.module == "ctypes":
        if any(alias.name == "wintypes" for alias in node.names):
            return "from ctypes import wintypes"
    return None


def _posix_only_name(node: ast.AST) -> str | None:
    """``killpg``/``SIGKILL``, which typeshed declares only off Windows."""
    if isinstance(node, ast.Attribute) and node.attr == "killpg":
        return f"{_root(node)}.killpg"
    if isinstance(node, ast.Attribute) and node.attr in ("SIGKILL", "SIGTERM"):
        return f"{_root(node)}.{node.attr}"
    return None


def _root(node: ast.AST) -> str:
    value = node.value if isinstance(node, ast.Attribute) else None
    return value.id if isinstance(value, ast.Name) else "?"


# --- AC-15: the timings cannot drift apart ---------------------------------


def test_the_worst_case_stop_fits_inside_the_default_shutdown_grace() -> None:
    """AC-15: a constant-to-constant assertion, so raising one alone fails.

    A job whose descendants refuse the polite request costs the grace plus
    **two** verifications: one after the polite tier, which is what turns
    "the launcher exited" into an answer about the tree, and one after the
    forced tier. If that ever exceeds ``SHUTDOWN_GRACE_S`` the agent would
    abandon a job it had promised to stop inside its own documented grace, and
    the promise would be a comment.

    Two, not one, is the price of the graceful path being verified rather than
    assumed (issue #125 S1). It is still 15 s against 30 s, and a verification
    that is going to pass resolves in microseconds.
    """
    grace = node_settings().SHUTDOWN_GRACE_S

    assert STOP_GRACE_S + 2 * STOP_VERIFY_TIMEOUT_S < grace
    assert STOP_VERIFY_POLL_S < STOP_VERIFY_TIMEOUT_S


# --- ProcessTree: the degraded modes (AC-12) -------------------------------


class StubJob:
    """A job object the test controls, so the degraded modes are reachable."""

    def __init__(
        self,
        *,
        refuse_assign: bool = False,
        active: int = 0,
        held: set[int] | None = None,
    ) -> None:
        self.refuse_assign = refuse_assign
        self.active = active
        self.held = held
        self.assigned: list[int] = []
        self.terminated: list[int] = []
        self.closed = False

    def assign(self, pid: int, what: str = "the launched process") -> bool:
        self.assigned.append(pid)
        if self.held is not None:
            self.held.add(pid)
        return not self.refuse_assign

    def terminate(self, exit_code: int) -> bool:
        self.terminated.append(exit_code)
        self.active = 0
        if self.held is not None:
            self.held.clear()
        return True

    def active_processes(self) -> int:
        if self.held is not None:
            return len(self.held)
        return self.active

    def members(self) -> set[int] | None:
        if self.held is not None:
            return set(self.held)
        return {self.active} if self.active else set()

    def close(self) -> None:
        self.closed = True


def a_finished_process() -> subprocess.Popen:
    """A ``Popen`` that has already exited; ``pid`` is all the tree needs."""
    return subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stub_job(job: "StubJob") -> JobFactory:
    """A :data:`JobFactory` that hands back the job the test controls."""

    def factory(_log: Callable[..., None]) -> JobHandle:
        return job

    return factory


def _no_job(_log: Callable[..., None]) -> None:
    """A :data:`JobFactory` for "this host has no job object at all"."""
    return None


def test_a_live_job_object_is_the_mode_on_windows() -> None:
    if sys.platform != "win32":
        pytest.skip(NO_JOB_OBJECTS)
    job = StubJob()

    tree = ProcessTree(
        a_finished_process(), log=lambda *_a: None, create_job=_stub_job(job)
    )

    assert tree.control_mode == CONTROL_MODE_JOB
    assert job.assigned, "the launched process was never assigned to the job"
    tree.close()
    assert job.closed


@pytest.mark.skipif(sys.platform != "win32", reason=NO_JOB_OBJECTS)
def test_a_refused_assignment_downgrades_to_the_sweep(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """S2: a job the kernel would not take must not be reported as holding the tree.

    A job with no members answers every accounting query with zero, so keeping
    one makes :meth:`ProcessTree.verify` report an empty tree because nothing
    was ever in it -- a vacuous success, and the most expensive kind, because
    the grandchild holding the GPUs is still running. Issue #125 §4 Q2 requires
    a WARNING and a fall back to ``taskkill /T /F``; nothing tested that, and
    ``StubJob(refuse_assign=...)`` existed unused.
    """
    job = StubJob(refuse_assign=True)

    with caplog.at_level("WARNING", logger="agent.process"):
        tree = ProcessTree(
            a_finished_process(), log=lambda *_a: None, create_job=_stub_job(job)
        )

    assert tree.control_mode == CONTROL_MODE_TASKKILL
    assert job.closed, "an empty job handle is still a handle, and still leaks"
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("refused" in line and "taskkill" in line for line in warnings), warnings

    # And the vacuous leg is gone: the forced tier no longer asks a job that
    # holds nothing, because the only thing it would have terminated is itself.
    tree.stop_forced()
    assert job.terminated == [], "the empty job was still asked to terminate"
    tree.close()


@pytest.mark.skipif(sys.platform != "win32", reason=NO_JOB_OBJECTS)
def test_a_job_object_that_cannot_be_created_falls_back_to_taskkill(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC-12: the fallback is real and loud, and it names the mechanism."""

    def refuse(_log: Callable[..., None]) -> JobHandle:
        raise OSError(5, "Access is denied")

    with caplog.at_level("WARNING", logger="agent.process"):
        tree = ProcessTree(
            a_finished_process(), log=lambda *_a: None, create_job=refuse
        )

    assert tree.control_mode == CONTROL_MODE_TASKKILL
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("taskkill" in r.getMessage() for r in warnings), (
        "a degraded node must announce itself: the launch line is the only "
        f"place an operator sees it. Got: {[r.getMessage() for r in warnings]}"
    )
    tree.close()


@pytest.mark.skipif(sys.platform != "win32", reason=NO_JOB_OBJECTS)
def test_no_job_object_and_no_taskkill_is_an_error_naming_the_consequence(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-12: the second fallback says the tree MAY HAVE SURVIVED."""
    monkeypatch.setattr("agent.process.shutil.which", lambda _name: None)

    with caplog.at_level("ERROR", logger="agent.process"):
        tree = ProcessTree(
            a_finished_process(),
            log=lambda *_a: None,
            create_job=_no_job,
        )

    assert tree.control_mode == CONTROL_MODE_LEADER
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert any("MAY HAVE SURVIVED" in r.getMessage() for r in errors)
    tree.close()


def test_posix_always_claims_the_process_group_and_never_a_job() -> None:
    """The POSIX half must not even ask for a job object."""
    if sys.platform == "win32":
        pytest.skip(NO_JOB_OBJECTS)

    asked: list[str] = []

    def record(_log: Callable[..., None]) -> None:
        asked.append("called")
        return None

    tree = ProcessTree(a_finished_process(), log=lambda *_a: None, create_job=record)

    assert tree.control_mode == CONTROL_MODE_GROUP
    assert asked == [], "POSIX has no job object and must not open one"
    tree.close()


# --- verify() is a read, not a hope (AC-11) -------------------------------


def test_verify_is_true_for_a_process_that_has_already_exited() -> None:
    """The trivial positive, asked the way production asks it.

    ``verify`` speaks about a tree it has *looked at*, and looking requires a
    live launcher to enumerate from -- so a tier runs first, exactly as
    ``_stop_tree`` does. Asking before that is asking about a tree nobody has
    inspected, and the honest answer is "cannot confirm" rather than "clean".
    """
    proc = a_finished_process()
    proc.wait(timeout=30)

    tree = ProcessTree(proc, log=lambda *_a: None)
    tree.stop_gracefully()

    assert tree.verify(5.0) is True
    tree.close()


@pytest.mark.skipif(sys.platform != "win32", reason=NO_JOB_OBJECTS)
def test_verify_is_false_while_the_job_still_holds_a_member() -> None:
    """AC-11: the job's own membership is what the negative case turns on."""
    proc = a_finished_process()
    proc.wait(timeout=30)
    job = StubJob(held={4242})

    tree = ProcessTree(proc, log=lambda *_a: None, create_job=_stub_job(job))

    assert tree.verify(0.2) is False
    tree.close()


def test_verify_reports_a_leader_that_will_not_be_reaped() -> None:
    """AC-11's negative case: the timeout branch, on both platforms.

    There is no unstoppable process to build -- ``SIGKILL`` and
    ``TerminateJobObject`` are not negotiable -- so the branch is reached
    through a ``Popen`` whose ``wait`` cannot complete. Asserting the timeout
    path is the point; asserting it with a real unstoppable process would be a
    test that cannot pass.
    """
    proc = a_finished_process()
    proc.wait(timeout=30)

    class NeverReaped:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired("fake", timeout or 0)

    tree = ProcessTree(proc, log=lambda *_a: None)
    # The stub replaces the process the tree holds; `pid` is all the
    # verification reads when there is no job object accounting to consult.
    tree._proc = cast("subprocess.Popen", NeverReaped(proc.pid))

    assert tree.verify(0.05) is False
    tree.close()


# --- a broken probe must fail loud, never open (N1, N2) -------------------


class BrokenSnapshot:
    """``kernel32`` whose snapshot call returns the documented failure value.

    The failure value is ``INVALID_HANDLE_VALUE``, which with
    ``restype = wintypes.HANDLE`` arrives as **18446744073709551615**, not
    ``-1``. Comparing it against ``-1`` is never true on 64-bit, so a broken
    snapshot walks straight through the check, ``Process32FirstW`` fails, and
    the caller is handed an empty host -- which reads as "the tree is gone"
    (issue #125 N1).
    """

    def __init__(self, handle: int) -> None:
        self.handle = handle
        self.first_called = False

    def CreateToolhelp32Snapshot(self, flags: int, pid: int) -> int:  # noqa: N802
        return self.handle

    def Process32FirstW(self, snapshot: int, entry: Any) -> int:  # noqa: N802
        self.first_called = True
        return 0

    def Process32NextW(self, snapshot: int, entry: Any) -> int:  # noqa: N802
        return 0

    def CloseHandle(self, handle: int) -> int:  # noqa: N802
        return 1


def install_broken_snapshot(monkeypatch: pytest.MonkeyPatch) -> BrokenSnapshot:
    """Make every process snapshot fail, the way a denied one would.

    The real ``ctypes`` and ``wintypes`` are kept in the cached tuple -- only
    ``kernel32`` is faked -- because the failure path reports
    ``get_last_error()`` and that call is part of what is under test.
    """
    import ctypes  # local: the Windows-only stdlib, as agent/process.py does
    import subprocess as sp
    from ctypes import wintypes

    assert sp is subprocess
    invalid = ctypes.c_void_p(-1).value
    assert invalid == 18446744073709551615, "the point of the fixture"
    kernel32 = BrokenSnapshot(invalid)
    real_toolhelp = process_module._toolhelp()
    assert real_toolhelp is not None
    ctypes_module, wintypes_module, _kernel32, _entry, _invalid = real_toolhelp
    assert ctypes_module is ctypes and wintypes_module is wintypes
    monkeypatch.setattr(
        process_module, "_toolhelp", lambda: (ctypes, wintypes, kernel32, None, invalid)
    )
    return kernel32


@pytest.mark.skipif(sys.platform != "win32", reason=NO_JOB_OBJECTS)
def test_a_failed_snapshot_is_unknown_not_an_empty_host(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """N1: the probe must not fail open.

    ``None`` means "could not enumerate"; ``{}`` means "the host is empty".
    Collapsing them is how a broken probe becomes a clean stop report for a
    tree that is still running, which is the worst direction this code can
    fail in.
    """
    kernel32 = install_broken_snapshot(monkeypatch)

    with caplog.at_level("WARNING", logger="agent.process"):
        result = process_module._live_processes()

    assert result is None, "a failed snapshot must not read as an empty host"
    assert not kernel32.first_called, (
        "INVALID_HANDLE_VALUE was not recognised, so the code walked on into a "
        "call that could only fail"
    )
    assert any("cannot be confirmed" in r.getMessage() for r in caplog.records), (
        "a probe that cannot answer must say so: "
        f"{[r.getMessage() for r in caplog.records]}"
    )


@pytest.mark.skipif(sys.platform != "win32", reason=NO_JOB_OBJECTS)
def test_a_failed_snapshot_makes_verify_report_a_tree_it_cannot_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N1, at the level the executor reads: ``False``, immediately, not at the deadline.

    Polling a broken probe to its deadline would only turn a broken answer into
    a slower broken answer.
    """
    proc = a_finished_process()
    proc.wait(timeout=30)
    job = StubJob()
    tree = ProcessTree(proc, log=lambda *_a: None, create_job=_stub_job(job))
    install_broken_snapshot(monkeypatch)
    tree._tracked = {12345}

    started = time.monotonic()
    assert tree.verify(30.0) is False
    assert (
        time.monotonic() - started < 1.0
    ), "an unreadable tree is not going to become readable by waiting"
    tree.close()


@pytest.mark.skipif(sys.platform != "win32", reason=NO_JOB_OBJECTS)
def test_a_library_that_cannot_be_loaded_does_not_escape_the_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N2: an exception out of here would unwind ``execute_job`` with no state written.

    The module docstring promises "we report a stop we cannot fully confirm"
    rather than "a crash on the stop path"; this is the assertion for that
    promise, with the failure mode QA measured -- ``WinDLL`` raising
    ``OSError: [Errno 126]`` when the library is missing.
    """

    def refuse() -> Any:
        raise OSError(126, "The specified module could not be found")

    monkeypatch.setattr(process_module, "_toolhelp", refuse)

    assert process_module._live_processes() is None
    assert process_module._descendant_pids(1) is None


@pytest.mark.skipif(sys.platform != "win32", reason=NO_JOB_OBJECTS)
def test_the_toolhelp_handle_is_resolved_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N2: re-resolving ``WinDLL`` per probe cost 560 ms across a 5 s verify."""
    import agent.process as module

    module._toolhelp.cache_clear()
    try:
        first = module._toolhelp()
        assert first is not None
        second = module._toolhelp()
        assert second is first
        assert module._toolhelp.cache_info().hits >= 1
    finally:
        module._toolhelp.cache_clear()
    assert monkeypatch is not None


@pytest.mark.skipif(sys.platform != "win32", reason=NO_JOB_OBJECTS)
async def test_the_job_reports_which_pids_it_holds_not_only_how_many(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """N6: the two legs of ``_outstanding`` must cover the same processes.

    One query answers "which", and it is what lets the adoption step skip a
    member the job already holds. Re-assigning a member fails with
    ``ERROR_ACCESS_DENIED``, and an earlier version of this code did exactly
    that: a warning per member, on every *healthy* run where the job had
    already inherited the tree.
    """
    process = build_tree(tmp_path, with_shell=False)
    proc = spawn_tree(process, creationflags=CREATE_NEW_PROCESS_GROUP)
    stop = ProcessTree(proc, log=lambda *_a: None)
    # The tree is enumerated at *stop* time, so the descendants have to exist
    # first; this is the ordering production has too, and it is why an earlier
    # version of this test saw an empty tree.
    await wait_until(
        lambda: len(process.recorded()) >= process.expected_pids,
        f"the tree never started: {process.pids_file}",
    )
    try:
        assert stop._job is not None
        with caplog.at_level("WARNING", logger="agent.process"):
            stop._remember_tree()

        assert stop._job.members(), "the job holds nothing at all"
        assert stop._job.active_processes() == len(
            stop._job.members() or ()
        ), "the count and the list disagree, which is the N6 coverage gap"
        assert stop._tracked, "nothing was enumerated under the launcher"
        assert not any(
            "could not be adopted" in r.getMessage() for r in caplog.records
        ), (
            "the adopting leg re-assigned pids the job already held: "
            f"{[r.getMessage() for r in caplog.records]}"
        )
    finally:
        stop.stop_forced()
        stop.verify(10.0)
        stop.close()


@pytest.mark.skipif(sys.platform != "win32", reason=NO_JOB_OBJECTS)
def test_the_forced_tier_runs_the_job_and_the_independent_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D6: neither mechanism alone is the claim, so both must run."""
    proc = a_finished_process()
    proc.wait(timeout=30)
    job = StubJob(active=1)
    tree = ProcessTree(proc, log=lambda *_a: None, create_job=_stub_job(job))
    sweeps: list[list[str]] = []

    def record_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        sweeps.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("agent.process.subprocess.run", record_run)

    tree.stop_forced()

    assert job.terminated, "TerminateJobObject is the kernel-enforced tier"
    assert len(sweeps) == 1, "taskkill /T /F is the independent sweep"
    assert sweeps[0][1:] == ["/T", "/F", "/PID", str(proc.pid)]


# --- the tiers, against a real tree (Q8c: both arms, both directions) -----


def spawn_tree(tree: Tree, **flags: Any) -> subprocess.Popen:
    """Run a tree's script directly, with no shell in between.

    The tree is built with ``with_shell=False`` for this path, so its parent
    records no ``getppid()`` and the pid it would have written -- **pytest
    itself** -- is never among the pids a test asserts is dead (issue #125 S5).
    """
    return subprocess.Popen(
        [sys.executable, str(tree.script), "parent", str(tree.pids_file)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **flags,
    )


async def wait_for_recorded(tree: Tree) -> list[int]:
    """Block until the tree has written ``expected_pids`` of them, then read all.

    Waiting on the *property* rather than sleeping a fixed fraction of a
    second: the sleep would have to outlast the grandchild's interpreter
    start-up, and a fixed sleep that is too short produces a test that fails on
    a loaded machine and passes on an idle one. Waiting for the **exact** count
    rather than a floor is what stops the Windows arm from quietly asserting
    on two of the three ranks -- the rank that orphans is the rank nobody
    checks (issue #125 S3).
    """
    await wait_until(
        lambda: len(tree.recorded()) >= tree.expected_pids,
        f"the tree recorded {len(tree.recorded())} of {tree.expected_pids} "
        f"pids in {tree.pids_file}",
    )
    return tree.recorded()


async def assert_no_survivors(recorded: list[int]) -> None:
    """Every recorded pid must be gone, load-safely and then hard.

    ``psutil.pid_exists`` is the one probe that works on both platforms.
    ``os.kill(pid, 0)`` works but the sibling ``os.kill(pid, 9)`` is
    ``TerminateProcess(handle, 9)`` on Windows, and ``OpenProcess`` hands back
    a handle for a pid that has already been reaped -- so neither is a
    liveness question (issue #125 C4).
    """
    for pid in recorded:

        def gone(watched: int = pid) -> bool:
            return not psutil.pid_exists(watched)

        await wait_until(gone, f"pid {pid} survived the stop")
        assert psutil.pid_exists(pid) is False


@pytest.mark.skipif(sys.platform == "win32", reason=NO_CONSOLE_CONTROL)
async def test_killpg_reaches_a_process_that_ignored_sigterm(
    tmp_path: Path,
) -> None:
    """The POSIX arm of AC-10: SIGTERM is cooperative, SIGKILL is not."""
    tree = build_tree(tmp_path, refuse_polite=True, with_shell=False)
    proc = spawn_tree(tree, start_new_session=True)
    # The tree is constructed **before** waiting for the descendants, which is
    # the order execute_job uses and the only order in which the mechanism under
    # test exists.
    stop = ProcessTree(proc, log=lambda *_a: None)
    recorded = await wait_for_recorded(tree)

    stop.stop_gracefully()
    stop.stop_forced()

    assert stop.verify(5.0) is True
    await assert_no_survivors(recorded)
    stop.close()


@pytest.mark.skipif(sys.platform != "win32", reason=NO_JOB_OBJECTS)
async def test_ctrl_break_is_cooperative_and_the_job_object_is_not(
    tmp_path: Path,
) -> None:
    """The Windows arm of AC-10, end to end against the real kernel.

    A child that installs ``SIG_IGN`` for ``SIGBREAK`` survives
    ``CTRL_BREAK_EVENT`` indefinitely (measured: two events did not kill it),
    which is why the forced tier is mandatory rather than a nicety. The
    assertion that ``verify`` says ``False`` in between is what makes the
    second one meaningful: otherwise a test where the polite tier happened to
    work would pass either way.
    """
    tree = build_tree(tmp_path, refuse_polite=True, with_shell=False)
    proc = spawn_tree(tree, creationflags=CREATE_NEW_PROCESS_GROUP)
    # Constructed before the descendants exist, exactly as execute_job does.
    stop = ProcessTree(proc, log=lambda *_a: None)
    recorded = await wait_for_recorded(tree)
    assert len(recorded) >= 2, recorded
    try:
        stop.stop_gracefully()
        await asyncio.sleep(0.5)

        assert stop.verify(0.2) is False, (
            "a tree that ignores CTRL_BREAK_EVENT must not verify as gone; if "
            "this fails the polite tier is stopping something it had no "
            "business stopping"
        )

        stop.stop_forced()
        assert stop.verify(5.0) is True
        await assert_no_survivors(recorded)
    finally:
        stop.close()
