"""
The platform-dependent half of job execution: which shell runs a job, the
launch flags that make the job its own process group, and how a whole process
tree is asked to stop and then *verified* to have stopped.

Everything platform-independent -- validation, the child environment, GPU
allocation, log capture, terminal state -- stays in ``agent/executor.py``, and
this module must not grow any of it. The split exists because "what runs the
job" and "how a tree of processes is stopped" are the only two things that
differ between a Linux box and a Windows box, and they are the two things
that were written for one of them and shipped to both (issue #125).

Three rules keep the halves honest:

* **stdlib only.** The agent is file-only by decision (issue #92), so nothing
  here may import a network module. ``ctypes`` is stdlib and reads no
  protocol; it is imported *inside* the Windows methods so a POSIX agent never
  loads it at all.
* **platform API sits behind a ``sys.platform`` guard, never behind
  ``hasattr``.** ``ctypes.WinDLL`` does not exist on Linux and ``os.killpg``
  does not exist on Windows, and ``mypy .`` is an acceptance criterion on
  *both* platforms (issue #125 AC-1, AC-2). A ``getattr`` probe would silence
  the error and hide the answer at the same time -- the defect issue #125 §1.2
  is about -- so each platform's bodies are guarded instead, and mypy's
  reachability analysis drops the other one. Nothing here needs a
  ``# type: ignore`` and ``pyproject.toml`` is unchanged.
* **"we sent a signal" is not "the tree is gone".** Every stop is followed by
  :meth:`ProcessTree.verify`, which reads a property of the running system
  rather than assuming the call worked.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Final, Literal, Protocol

logger = logging.getLogger(__name__)

# --- timings ---------------------------------------------------------------

#: How long the polite tier is given before the forced tier runs. Deliberately
#: **not** a setting: issue #106 §4.1 assigns the escalation policy to #99, and
#: #106 §Q10 measured its "process group gone within 10 s" budget against this
#: exact value. Making it configurable now would put a fleet-wide number into
#: ``nodes/agent.toml`` before anyone has agreed what it should be, and would
#: invalidate a measurement for no reason. It is the value #98 hard-coded.
STOP_GRACE_S: Final[float] = 5.0

#: How long the forced tier is given to *prove* the tree is gone. Separate
#: from the grace on purpose: the grace is the job's chance to clean up after
#: itself, and this is the agent's chance to find out that it did not. The
#: invariant ``STOP_GRACE_S + STOP_VERIFY_TIMEOUT_S < SHUTDOWN_GRACE_S`` is
#: asserted by a test so neither number can be raised alone.
STOP_VERIFY_TIMEOUT_S: Final[float] = 5.0

#: The Windows verification polls the job object's own accounting. The query is
#: a read of kernel-maintained counters, so 25 ms is free, and it bounds the
#: overshoot on the deadline above to 2%.
STOP_VERIFY_POLL_S: Final[float] = 0.025

#: ``TerminateJobObject``'s exit code. Measured: the leader then waits with
#: status 1. It is **not** -9 and **not** ``0xC000013A``, both of which are
#: already spoken for -- -9 is POSIX ``SIGKILL`` and ``0xC000013A`` is what a
#: Windows process killed by ``CTRL_BREAK_EVENT`` reports. ``exit_code`` carries
#: whatever the OS really returned and never a sentinel (issue #125 D8).
FORCED_STOP_EXIT_CODE: Final[int] = 1

#: Why a stop happened. **Closed at one value on purpose.** #99 owns the
#: operator-facing cancel protocol and #106 the ``cancel`` stop reason; both
#: need this to become ``Literal["shutdown", "cancel"]`` with a
#: ``cancel_reason`` carried alongside, and neither may report "the operator
#: cancelled this" for a job the operator never touched (#106 §5.7). The seam
#: exists now so that extension is a type widening and not a refactor.
StopReason = Literal["shutdown"]

#: One token naming the mechanism that is actually live, logged on every
#: launch. An operator reading a stop that only killed the launcher needs to
#: see which of the degraded modes they are in without turning on DEBUG.
#: ``taskkill fallback`` and ``leader only`` are the two degraded Windows modes
#: and are not in issue #125 §6.1's three: a node without ``taskkill.exe``
#: *and* without a usable job object is a third state, and reporting it as
#: either of the other two would be a lie in the only line an operator reads.
CONTROL_MODE_GROUP: Final[str] = "process group"
CONTROL_MODE_JOB: Final[str] = "job object"
CONTROL_MODE_TASKKILL: Final[str] = "taskkill fallback"
CONTROL_MODE_LEADER: Final[str] = "leader only"

#: Ceiling on the ``taskkill`` sweep. It only runs during a stop and it talks
#: to an in-process tree rather than to a network, but an unbounded
#: ``subprocess.run`` here would make the *bounded* stop unbounded, which is
#: the property the whole stop path exists to have.
TASKKILL_TIMEOUT_S: Final[float] = 10.0

# --- shell resolution ------------------------------------------------------

#: The values ``AGENT_SHELL`` accepts, in the order the rejection message lists
#: them. ``auto`` first because it is the default and the reason the others
#: exist.
ACCEPTED_SHELLS: Final[tuple[str, ...]] = (
    "auto",
    "sh",
    "dash",
    "bash",
    "cmd",
    "pwsh",
    "powershell",
)

#: ``basename -> (program to resolve, fixed flags, assemble one string?)``.
#: The flags are a table and not a setting: a configurable flag set is exactly
#: how a quoting contract gets broken, and there is one correct flag set per
#: shell (issue #125 D4). ``sh``/``dash``/``bash`` read a real argv; ``cmd``
#: and ``powershell`` parse ``lpCommandLine`` themselves.
#:
#: The ``.exe`` spellings are here as well as the bare names because an
#: operator naming a path on Windows will write the file name -- and the
#: path an operator most likely has is Git's ``sh.exe``, which is exactly the
#: program this table must recognise as "an argv shell" if it is to be usable
#: by name at all (issue #125 Q1).
_SHELL_TABLE: Final[dict[str, tuple[str, tuple[str, ...], bool]]] = {
    "sh": ("sh", ("-c",), False),
    "sh.exe": ("sh", ("-c",), False),
    "dash": ("dash", ("-c",), False),
    "dash.exe": ("dash", ("-c",), False),
    "bash": ("bash", ("-c",), False),
    "bash.exe": ("bash", ("-c",), False),
    "cmd": ("cmd.exe", ("/d", "/s", "/c"), True),
    "cmd.exe": ("cmd.exe", ("/d", "/s", "/c"), True),
    "pwsh": ("pwsh.exe", ("-NoProfile", "-NonInteractive", "-Command"), True),
    "pwsh.exe": ("pwsh.exe", ("-NoProfile", "-NonInteractive", "-Command"), True),
    "powershell": (
        "powershell.exe",
        ("-NoProfile", "-NonInteractive", "-Command"),
        True,
    ),
    "powershell.exe": (
        "powershell.exe",
        ("-NoProfile", "-NonInteractive", "-Command"),
        True,
    ),
}

#: ``auto``: the platform default, decided by what an operator's ``command``
#: string already means on that host. On Windows that is ``cmd.exe`` --
#: ``%VAR%``, ``&&``, ``||``, ``> file`` and ``cd /d`` are the shape of every
#: batch file anyone has written for a Windows box, and choosing PowerShell
#: silently redefines what that string means (``%VAR%`` stops expanding, and
#: ``&&`` only chains pipelines in PowerShell 7, not in the 5.1 that ships with
#: Windows). On POSIX it is ``/bin/sh``.
#:
#: Git for Windows' ``sh.EXE`` is **not** the default, although it is usually on
#: ``PATH``: it is present only if someone installed Git, and it is an MSYS
#: build with different path translation (``/c/Users`` vs ``C:\Users``),
#: quoting and signal handling from the ``/bin/sh`` a submitted command was
#: written against. A shell chosen by an accident of ``PATH`` is not a
#: contract. It stays selectable by name -- "not the default" and "not
#: supported" are different claims.
_AUTO_CANDIDATE: Final[tuple[str, tuple[str, ...], bool]] = (
    ("cmd.exe", ("/d", "/s", "/c"), True)
    if sys.platform == "win32"
    else ("/bin/sh", ("-c",), False)
)


@dataclass(frozen=True)
class ShellSpec:
    """One resolved shell: what runs, how it is handed the command, and how.

    ``program`` is what ``shutil.which`` returned, or the bare name when it
    returned nothing. The distinction is not cosmetic: the executor resolves
    it *before* launch and reports ``launch_failed:shell_not_found`` rather
    than letting ``Popen`` raise, so "the agent has no shell" can never be
    confused with "your command does not exist" (issue #125 Q5).
    """

    program: str
    flags: tuple[str, ...]
    single_string: bool


def resolve_shell(configured: str) -> ShellSpec:
    """Map the ``AGENT_SHELL`` value to a program, its fixed flags, and the
    argv convention.

    Resolution is ``shutil.which`` and never ``%ComSpec%``: ``PATH`` is what
    the submitted ``command`` itself is resolved against, and the shell and
    the command have to be found the same way or a job can be launched by a
    shell its own command could not have found (issue #125 D5).

    Raises:
        ValueError: listing every accepted value, because this message reaches
            every node in the fleet -- ``nodes/agent.toml`` replicates.
    """
    value = configured.strip()

    if value.lower() == "auto":
        program, flags, single_string = _AUTO_CANDIDATE
        return ShellSpec(shutil.which(program) or program, flags, single_string)

    bare = _SHELL_TABLE.get(value.lower())
    if bare is not None:
        program, flags, single_string = bare
        return ShellSpec(shutil.which(program) or program, flags, single_string)

    # Not one of the bare names: an operator-supplied path. Its basename
    # decides how the command line is assembled and the path itself is what
    # gets resolved -- two nodes may legitimately have it in different places,
    # and which() on an absolute path is the only portable answer.
    entry = _SHELL_TABLE.get(Path(value).name.lower())
    if entry is None:
        raise ValueError(
            f"AGENT_SHELL={configured!r} names no shell this agent can run. "
            f"Accepted values are {', '.join(ACCEPTED_SHELLS)}, or a path whose "
            f"name is one of {', '.join(sorted(_SHELL_TABLE))}."
        )
    _, flags, single_string = entry
    return ShellSpec(shutil.which(value) or value, flags, single_string)


def shell_argv(spec: ShellSpec, command: str) -> list[str] | str:
    """The argument handed to ``subprocess.Popen``.

    A **list** when the program parses a real argv, one assembled **string**
    when it parses ``lpCommandLine`` itself. The distinction is a property of
    the shell's parser, not of the platform, which is why Git-bash's
    ``sh.exe`` keeps the list form even on Windows (issue #125 D2).

    On Windows ``CreateProcess`` takes a single command line, and Python
    composes it from a list with ``list2cmdline`` -- the MSVCRT /
    ``CommandLineToArgvW`` quoting rules. ``cmd.exe`` does not use those rules:
    it reads a literal ``"``, so a quoted ``command`` arrives as ``\\"`` and
    *every* command that quotes anything fails. Measured (issue #125 §1.3)::

        Popen(["cmd.exe", "/d", "/s", "/c", '"C:\\\\...\\\\sh.exe" -c "exit 7"'])
            -> exit 1, '"\\\\"C:\\\\Program Files..." is not recognized'
        Popen('cmd.exe /d /s /c "C:\\\\...\\\\sh.exe" -c "exit 7"')
            -> exit 7
    """
    if not spec.single_string:
        return [spec.program, *spec.flags, command]
    return f'"{spec.program}" {" ".join(spec.flags)} "{command}"'


#: ``CREATE_NEW_PROCESS_GROUP`` from ``<winbase.h>``, pinned as a literal
#: because ``subprocess`` only defines it on Windows and the POSIX type
#: checker must not read an attribute that does not exist there.
CREATE_NEW_PROCESS_GROUP: Final[int] = 0x00000200


def launch_flags() -> dict[str, object]:
    """``Popen`` keyword arguments that put the job in its own stoppable group.

    POSIX ``setsid`` and Windows ``CREATE_NEW_PROCESS_GROUP`` are the same idea
    reached two ways: the job leads a group whose id is its own pid, so one
    signal aimed at the group reaches the launcher and everything it started
    that did not leave the group behind. Windows needs a second mechanism too
    -- the Job Object -- because a group is only as good as the processes still
    in it, and a Job Object is enforced by the kernel at process creation
    instead of walked by a tool at kill time.
    """
    if sys.platform == "win32":
        return {"creationflags": CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


# --- process tree ----------------------------------------------------------

#: Job Object constants from ``<jobapi2.h>``, as literals for the same reason
#: ``CREATE_NEW_PROCESS_GROUP`` is. ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` is
#: the load-bearing one: the kernel kills everything in the job when the last
#: handle to it closes, and the OS closes that handle when the agent dies --
#: which is the only thing that can stop a Windows agent's tree, because
#: Windows has no ``SIGTERM`` for the agent to catch.
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: Final[int] = 0x00002000
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION: Final[int] = 1
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION: Final[int] = 9
_PROCESS_TERMINATE: Final[int] = 0x0001
_PROCESS_SET_QUOTA: Final[int] = 0x0100


class JobHandle(Protocol):
    """What a Windows Job Object has to offer to be usable here.

    A protocol rather than a base class so the stub AC-12 injects can be a
    two-line class in a test, and so the POSIX half of this module needs no
    knowledge of Windows types at all.
    """

    def assign(self, pid: int) -> bool:
        """Put ``pid`` in the job. ``False`` when the kernel refused."""

    def terminate(self, exit_code: int) -> bool:
        """Kill every process in the job. ``False`` when the kernel refused."""

    def active_processes(self) -> int:
        """How many processes are still running in the job, right now."""

    def close(self) -> None:
        """Drop the handle, which fires ``KILL_ON_JOB_CLOSE`` if any survive."""


#: The injectable seam for :class:`JobHandle` creation. Takes the logging
#: callable so an implementation can say why it could not do its job; returns
#: ``None`` for "no job here" and raises for "I tried and failed", which the
#: caller turns into the degraded mode.
JobFactory = Callable[[Callable[..., None]], "JobHandle | None"]


class _JobObject:
    """One Windows Job Object, held open for as long as this agent needs the tree.

    Created with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` so that an agent which
    dies without a chance to clean up still takes its job with it: the OS
    closes the handle and the kernel kills everything in the job. That is the
    one place Windows is *better* than POSIX, where a ``SIGKILL``ed agent
    orphans a process group that nothing will ever signal again.

    ``ActiveProcesses`` from ``JobObjectBasicAccountingInformation`` is the
    only read-only answer to "is anything still running in this tree", and it
    is what makes :meth:`active_processes` a verification rather than a hope.

    Every method starts by refusing on a non-Windows host. That is not
    defensiveness for its own sake: it is what puts the whole ``ctypes`` body
    inside a block mypy's reachability analysis drops on Linux, so the POSIX
    type checker never has to know that ``ctypes.WinDLL`` does not exist there
    and the Windows one never has to know that ``os.killpg`` does not. One
    guard buys a clean ``mypy .`` on both platforms instead of a
    ``# type: ignore``.
    """

    def __init__(self) -> None:
        self._handle: int | None = None
        self._kernel32: Any = None

    def _library(self) -> Any:
        """``kernel32`` with every prototype this module calls declared.

        ``use_last_error`` is what makes ``ctypes.get_last_error()`` mean
        something: without it a failed call leaves whatever errno the thread
        happened to carry, and the message then names a number that has
        nothing to do with the failure. An undeclared ``restype`` is worse --
        a ``HANDLE`` would come back truncated to ``int`` on a 64-bit host.
        """
        if sys.platform != "win32":
            raise RuntimeError("kernel32 is a Windows library")

        import ctypes
        from ctypes import wintypes

        if self._kernel32 is None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = wintypes.HANDLE
            kernel32.CreateJobObjectW.restype = handle
            kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            kernel32.SetInformationJobObject.restype = wintypes.BOOL
            kernel32.SetInformationJobObject.argtypes = [
                handle,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
            kernel32.AssignProcessToJobObject.argtypes = [handle, handle]
            kernel32.TerminateJobObject.restype = wintypes.BOOL
            kernel32.TerminateJobObject.argtypes = [handle, wintypes.UINT]
            kernel32.QueryInformationJobObject.restype = wintypes.BOOL
            kernel32.QueryInformationJobObject.argtypes = [
                handle,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
            ]
            kernel32.OpenProcess.restype = handle
            kernel32.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [handle]
            self._kernel32 = kernel32
        return self._kernel32

    def create(self) -> bool:
        """Create the job and arm ``KILL_ON_JOB_CLOSE``. ``False`` on refusal."""
        if sys.platform != "win32":
            return False
        import ctypes

        kernel32 = self._library()
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            logger.warning(
                "CreateJobObjectW failed (winerror %d)", ctypes.get_last_error()
            )
            return False
        self._handle = int(job)
        limits = self._extended_limit_information()
        if not kernel32.SetInformationJobObject(
            self._handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            # The job still works as a container; it just will not follow the
            # agent down. Worth a warning, not worth refusing the job over.
            logger.warning(
                "SetInformationJobObject(KILL_ON_JOB_CLOSE) failed (winerror %d); "
                "the job will not survive this agent",
                ctypes.get_last_error(),
            )
        return True

    def _extended_limit_information(self) -> Any:
        """``JOBOBJECT_EXTENDED_LIMIT_INFORMATION`` with only ``LimitFlags`` set.

        The layout is spelled out rather than pulled from a helper because
        ``ctypes`` ships the raw kernel, not the typed wrapper, and every
        field is part of the struct's size -- leaving one out would make
        ``sizeof`` wrong and the call would fail with a parameter error that
        says nothing useful.
        """
        if sys.platform != "win32":
            raise RuntimeError("JOBOBJECT_EXTENDED_LIMIT_INFORMATION is Windows")

        import ctypes
        from ctypes import wintypes

        class _BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class _ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimitInformation),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        return limits

    def assign(self, pid: int) -> bool:
        """Add a freshly launched process to the job.

        Assignment happens *after* ``CreateProcess`` returned, so a descendant
        spawned inside that window is structurally outside the job and the
        job's accounting never counts it. The window is milliseconds and 13
        measured runs produced no escape, which is why this is a structural
        residual rather than a reproduced failure -- and why
        :meth:`ProcessTree.stop_forced` also runs an independent sweep
        (issue #125 §1.6).

        ``OpenProcess`` rather than ``Popen``'s private ``_handle``: the two
        rights are named in the Win32 contract, and a handle the agent did not
        open is a handle it has to remember to close.
        """
        if sys.platform != "win32":
            return False
        import ctypes
        from ctypes import wintypes

        if self._handle is None:
            return False
        kernel32 = self._library()
        process = kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid
        )
        if not process:
            logger.warning(
                "OpenProcess(%d) failed (winerror %d); the job cannot hold it",
                pid,
                ctypes.get_last_error(),
            )
            return False
        try:
            ok = kernel32.AssignProcessToJobObject(
                self._handle, wintypes.HANDLE(process)
            )
        finally:
            kernel32.CloseHandle(process)
        if not ok:
            logger.warning(
                "AssignProcessToJobObject(%d) failed (winerror %d); nested jobs "
                "are unavailable on some hosts",
                pid,
                ctypes.get_last_error(),
            )
        return bool(ok)

    def terminate(self, exit_code: int) -> bool:
        if sys.platform != "win32":
            return False
        if self._handle is None:
            return False
        return bool(self._library().TerminateJobObject(self._handle, exit_code))

    def _accounting_information(self) -> Any:
        if sys.platform != "win32":
            raise RuntimeError("JOBOBJECT_BASIC_ACCOUNTING_INFORMATION is Windows")

        import ctypes
        from ctypes import wintypes

        class _BasicAccountingInformation(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_longlong),
                ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong),
                ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        return _BasicAccountingInformation()

    def active_processes(self) -> int:
        """The kernel's own count of processes still running in this job.

        ``TerminateJobObject`` is asynchronous in substance: this reads 0 on
        the very next query while the leader's ``wait()`` still has about
        0.3 s to go (measured, issue #125 §1.6). Those are two different facts
        and the caller needs both, which is why :meth:`ProcessTree.verify`
        asks for each separately.
        """
        if sys.platform != "win32":
            return 0
        import ctypes
        from ctypes import wintypes

        if self._handle is None:
            return 0
        kernel32 = self._library()
        accounting = self._accounting_information()
        returned = wintypes.DWORD(0)
        ok = kernel32.QueryInformationJobObject(
            self._handle,
            _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(accounting),
            ctypes.sizeof(accounting),
            ctypes.byref(returned),
        )
        if not ok:
            logger.warning(
                "QueryInformationJobObject failed (winerror %d); the verification "
                "falls back to the leader alone",
                ctypes.get_last_error(),
            )
            return 0
        return int(accounting.ActiveProcesses)

    def close(self) -> None:
        if sys.platform != "win32":
            return
        if self._handle is None:
            return
        self._library().CloseHandle(self._handle)
        self._handle = None


def _open_job_object(log: Callable[..., None]) -> JobHandle | None:
    """The default :data:`JobFactory`: ``None`` when the host says no.

    Creating and assigning are separate calls so the caller can report the two
    failures differently -- a host that refuses ``CreateJobObjectW`` has no
    quota privilege at all, while one that refuses ``AssignProcessToJobObject``
    usually has nested jobs disabled and is perfectly capable of the polite
    tier.
    """
    try:
        job = _JobObject()
        if not job.create():
            return None
    except OSError as exc:
        log("process tree control: job object unavailable (%s)", exc)
        return None
    return job


class ProcessTree:
    """Owns everything needed to stop one launched job's tree on this platform.

    POSIX  : the pgid is ``proc.pid`` (``start_new_session``);
             :meth:`stop_gracefully` is ``killpg(SIGTERM)``,
             :meth:`stop_forced` is ``killpg(SIGKILL)``, and
             :meth:`verify` is a bounded ``proc.wait()``.
    Windows: the process group is the ``CTRL_BREAK_EVENT`` target **and** a Job
             Object created with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` holds
             the tree; :meth:`stop_gracefully` is
             ``GenerateConsoleCtrlEvent``, :meth:`stop_forced` is
             ``TerminateJobObject(job, 1)`` followed by ``taskkill /T /F /PID``
             as an independent sweep, and :meth:`verify` polls
             ``ActiveProcesses == 0`` **and** waits for the leader.

    The job object comes from an injectable factory because the degraded mode
    has to be testable rather than assumed unreachable: a host with nested jobs
    disabled, or a container without ``PROCESS_SET_QUOTA``, is exactly the
    deployment where a silent fallback would cost the most.

    Both platforms are one class with guards rather than two classes behind a
    factory, because a name defined *only* inside an ``if sys.platform`` block
    is invisible to the other platform's type checker -- it would need a
    ``# type: ignore`` to be referenced at all, which is the very thing issue
    #125 exists to delete.
    """

    def __init__(
        self,
        proc: subprocess.Popen,
        *,
        log: Callable[..., None],
        create_job: JobFactory | None = None,
    ) -> None:
        self._proc = proc
        self._log = log
        self._job: JobHandle | None = None
        self._taskkill: str | None = None
        self._control_mode = CONTROL_MODE_GROUP
        self._closed = False
        if sys.platform == "win32":
            self._open_windows_tree(create_job or _open_job_object)

    def _open_windows_tree(self, create_job: JobFactory) -> None:
        """Arm the Windows tier, and say which of the four modes is live.

        Every degraded path is **announced**, never assumed: an operator
        reading a stop that only killed the launcher has to be able to tell
        that from a stop that worked, without turning on DEBUG.
        """
        self._taskkill = shutil.which("taskkill.exe")
        try:
            job = create_job(self._log)
            if job is not None:
                job.assign(self._proc.pid)
        except OSError as exc:
            logger.warning(
                "process tree control: job object unavailable (winerror %s); "
                "falling back to taskkill",
                exc,
            )
            job = None
        self._job = job
        if job is not None:
            self._control_mode = CONTROL_MODE_JOB
        elif self._taskkill is not None:
            self._control_mode = CONTROL_MODE_TASKKILL
            logger.warning(
                "process tree control: no job object; the forced stop will be "
                "taskkill /T /F alone, which walks parent/child links at kill "
                "time instead of being enforced at creation"
            )
        else:
            self._control_mode = CONTROL_MODE_LEADER
            logger.error(
                "process tree control: neither a job object nor taskkill.exe is "
                "available; a stop will kill the launcher only and the TREE MAY "
                "HAVE SURVIVED"
            )

    @property
    def control_mode(self) -> str:
        """Which mechanism is live -- one token for the launch log line."""
        return self._control_mode

    def stop_gracefully(self) -> None:
        """Ask the tree to stop, politely. Never waits; never raises.

        Cooperative on both platforms, and on Windows **only** on Windows:
        ``GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pgid)`` reaches through a
        ``cmd.exe`` launcher to a grandchild, because children inherit the
        group unless they create their own -- but a child that installed
        ``SetConsoleCtrlHandler(..., TRUE)`` or ``SIG_IGN`` for ``SIGBREAK``
        survives it indefinitely. Measured: two events did not kill it. That is
        why the forced tier is not optional.
        """
        if sys.platform == "win32":
            try:
                self._proc.send_signal(signal.CTRL_BREAK_EVENT)
            except (OSError, ValueError) as exc:
                # No console at all (a service, a scheduled task, a container)
                # is the expected cause, and the forced tier does not need one.
                self._log(
                    "CTRL_BREAK_EVENT to %d was not delivered: %s", self._proc.pid, exc
                )
            return
        try:
            os.killpg(self._proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            self._log("process group %d is already gone", self._proc.pid)

    def stop_forced(self) -> None:
        """Stop the tree without asking, because the polite tier was refused.

        Windows runs **two** mechanisms here and needs both: the job object is
        enforced by the kernel at process creation but has the
        post-``CreateProcess`` assignment window, and ``taskkill /T /F`` walks
        parent/child links at kill time but depends on an external binary that
        a stripped ``PATH`` can remove. Each covers the other's hole, and only
        the pair is a claim of "the tree is gone".

        The **sweep runs first**, which is the one place this departs from the
        literal order in issue #125 §4 Q2's table. ``taskkill /T`` walks parent
        and child links from the launcher, so it can only reach a descendant
        that escaped the job while the launcher is still alive to walk from --
        and ``TerminateJobObject`` kills the launcher. Measured while
        developing this: with the job terminated first, the sweep returns
        "no process found" and the escaped descendants survive, which makes the
        sweep inert exactly when it is needed. Terminating the job afterwards
        catches anything ``taskkill`` could not reach.
        """
        if sys.platform != "win32":
            try:
                os.killpg(self._proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                self._log("process group %d is already gone", self._proc.pid)
            return

        if self._taskkill is not None:
            self._sweep()
        if self._job is not None and not self._job.terminate(FORCED_STOP_EXIT_CODE):
            logger.warning(
                "TerminateJobObject failed for pid %d; the sweep carried the "
                "forced tier alone",
                self._proc.pid,
            )
        if self._taskkill is None:
            if self._job is None:
                logger.error(
                    "forced stop with no job object and no taskkill.exe: killing "
                    "pid %d alone; its children MAY HAVE SURVIVED",
                    self._proc.pid,
                )
                self._proc.kill()
            else:
                logger.warning(
                    "taskkill.exe did not resolve; the job object was the only "
                    "forced tier for pid %d",
                    self._proc.pid,
                )

    def _sweep(self) -> None:
        """``taskkill /T /F``: the sweep that is independent of the job object."""
        assert self._taskkill is not None
        try:
            completed = subprocess.run(
                [self._taskkill, "/T", "/F", "/PID", str(self._proc.pid)],
                capture_output=True,
                text=True,
                timeout=TASKKILL_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning(
                "the taskkill sweep for pid %d could not run: %s",
                self._proc.pid,
                exc,
            )
            return
        if completed.returncode != 0:
            self._log(
                "the taskkill sweep for pid %d exited %d: %s",
                self._proc.pid,
                completed.returncode,
                completed.stderr.strip(),
            )

    def verify(self, timeout_s: float) -> bool:
        """``True`` when the tree is verifiably gone. Blocks; run off the loop.

        Bounded on both platforms, and it waits on the *property* rather than
        sleeping for a fixed fraction of the timeout: a fixed sleep is a bet
        that the tree is gone by then, which is the assumption this method
        exists to remove.

        Windows asks two questions because they have different answers.
        ``ActiveProcesses == 0`` says nothing still runs *in this tree*;
        ``proc.wait()`` says the *launcher* has been reaped. Measured, the
        first reads 0 within microseconds of ``TerminateJobObject`` and the
        second takes about 0.3 s.

        POSIX can only ask the second. There is no portable "how many processes
        are in this group", and pretending otherwise would make the production
        claim stronger than the platform can support; the whole group is proved
        gone by a test that can see every pid instead.
        """
        deadline = time.monotonic() + timeout_s
        if self._job is not None:
            while self._job.active_processes() > 0:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(STOP_VERIFY_POLL_S)
            if self._job.active_processes() > 0:
                return False
        try:
            self._proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            return False
        return True

    def close(self) -> None:
        """Release the platform handles.

        On Windows this is what fires ``KILL_ON_JOB_CLOSE`` for anything that
        somehow outlived the stop, so the executor calls it on every path out of
        a job, not only the tidy one. Idempotent, because closing the job twice
        would close a handle number that may since have been reused.
        """
        if self._closed:
            return
        self._closed = True
        if self._job is not None:
            self._job.close()
            self._job = None
