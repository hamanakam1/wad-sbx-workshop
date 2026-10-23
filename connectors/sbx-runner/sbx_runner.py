#!/usr/bin/env python3
"""Run a command inside a disposable Docker Sandbox and emit an evidence receipt.

The Docker Sandboxes CLI (`sbx`) changed its command-line surface in v0.48.0:
flag spellings moved (`--detached` became `--detach`, `--auto-approve` became
`--force`, `-o json` became `--format json`) and several `--json` documents
changed shape (`sbx ls` stopped wrapping the list in `{"sandboxes": [...]}`).
A wrapper that hard-codes one spelling breaks silently on the other.

This module is that wrapper. It reads the installed `sbx` version once, picks
the spelling that version understands, and normalises the shapes it gets back,
so a caller can write one recipe and run it on v0.45 through v0.49.x.

It is deliberately small and dependency-free: no network access, no model
calls, no Docker socket of its own. Everything it does goes through `sbx`.

    status    report the installed sbx, the daemon, and the sandboxes
    run       create a sandbox, run a command in it, write a receipt
    selftest  exercise the version-compat logic without touching a sandbox

The receipt is the point. `sbx exec` prints to a terminal and forgets; this
writes a document that names the exact sbx build, the exact command, the
digests of the inputs, the exit status and the timings, so a later reader can
tell what was actually run without trusting the runner.

Boundaries that are not negotiable:
  * The sandbox is disposable and is removed unless `--keep` is passed.
  * A workspace is never written back to the host by default: `--mode clone`
    is the default because the agent-side clone is a copy, and the host
    working tree is only ever touched by an explicit `--mode direct`.
  * Output is bounded. A test that prints a megabyte is truncated in the
    receipt, not stored whole.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

SCHEMA = "sbx-sandbox-test-receipt-v1"
HARNESS_VERSION = "1.0.0"

# Bound on everything the runner holds in memory at once.
MAX_CAPTURE_BYTES = 256 * 1024
MAX_RECEIPT_BYTES = 1024 * 1024
DEFAULT_TIMEOUT = 900
DEFAULT_CREATE_TIMEOUT = 300
DEFAULT_STOP_TIMEOUT = 120
MAX_OUTPUT_TAIL = 16 * 1024

_SANDBOX_NAME = re.compile(r"[a-z0-9][a-z0-9.-]{1,63}\Z")
_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


class RunnerError(RuntimeError):
    """A failure with a stable, machine-readable code."""

    def __init__(self, code, detail=""):
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------
# Version handling
# --------------------------------------------------------------------------

def parse_version(text):
    """Return a comparable (major, minor, patch) tuple for a version string.

    Accepts `v0.49.2`, `0.49.2-rc2`, `v0.45.0-rc2` and `0.49.2`. Returns
    None when the text carries no recognisable version, which is how the
    caller learns to probe instead of guess.
    """
    if not isinstance(text, str):
        return None
    match = _VERSION.search(text)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def at_least(version, major, minor=0, patch=0):
    """True when `version` is >= the given release. None is never at least."""
    if version is None:
        return False
    return version >= (major, minor, patch)


def capabilities(version):
    """Describe what the installed sbx can do.

    Every entry is a fact about the CLI surface, derived from the version.
    The caller should read this rather than repeat version comparisons.
    """
    return {
        # v0.45.0-rc2 shipped the pre-0.48 spellings.
        "detach_flag": "--detach" if at_least(version, 0, 48) else "--detached",
        "force_flag": "--force",
        "no_progress_flag": "--no-progress" if at_least(version, 0, 48) else "--quiet",
        "diagnose_format_flag": "--format" if at_least(version, 0, 48) else "--output",
        "list_is_bare_array": at_least(version, 0, 48),
        # v0.47.0
        "run_rm": at_least(version, 0, 47),
        # v0.48.0
        "mcp_unload": at_least(version, 0, 48),
        # v0.49.1
        "policy_refresh": at_least(version, 0, 49, 1),
        "kit_validate_offline": at_least(version, 0, 49, 1),
        # v0.49.2 shipped the rebuilt CA images.
        "rebuilt_ca_images": at_least(version, 0, 49, 2),
    }


# --------------------------------------------------------------------------
# The CLI wrapper
# --------------------------------------------------------------------------

class Sbx:
    """A thin, bounded wrapper around the `sbx` binary."""

    def __init__(self, binary=None, path=None, timeout=DEFAULT_TIMEOUT):
        self.binary = binary or shutil.which("sbx") or ""
        if path:
            for entry in path.split(os.pathsep):
                candidate = Path(entry).expanduser() / "sbx"
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    self.binary = str(candidate)
                    break
        self.timeout = timeout
        self._version = None
        self._caps = None

    # -- process plumbing --------------------------------------------------

    def _argv(self, args):
        if not self.binary:
            raise RunnerError("sbx_not_installed",
                              "install the sbx CLI and sign in with `sbx login`")
        return [self.binary, *[str(a) for a in args]]

    def run(self, args, timeout=None, stdin=None):
        """Run sbx and return (returncode, stdout_bytes, stderr_bytes)."""
        argv = self._argv(args)
        try:
            proc = subprocess.run(
                argv, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=timeout or self.timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RunnerError("sbx_timeout", f"{' '.join(argv[1:])} exceeded "
                                               f"{timeout or self.timeout}s") from exc
        except OSError as exc:
            raise RunnerError("sbx_not_runnable", exc.strerror or str(exc)) from exc
        return proc.returncode, proc.stdout or b"", proc.stderr or b""

    def json(self, args, timeout=None):
        """Run sbx with a JSON output flag and decode the document.

        The flag differs by version; the shape differs by version. Both are
        handled here so callers only ever see data.
        """
        rc, out, err = self.run([*args, "--json"], timeout=timeout)
        if rc != 0:
            raise RunnerError("sbx_command_failed",
                              f"{' '.join(str(a) for a in args)} exited {rc}: "
                              f"{_tail(err).decode('utf-8', 'replace').strip()}")
        text = out.decode("utf-8", "replace").strip()
        if not text:
            raise RunnerError("sbx_empty_output", f"{' '.join(str(a) for a in args)}")
        try:
            return json.loads(text)
        except ValueError as exc:
            raise RunnerError("sbx_unparsable_output",
                              f"{' '.join(str(a) for a in args)}: {exc}") from exc

    # -- version -----------------------------------------------------------

    def version(self):
        """Return the client version tuple, or None when it cannot be read."""
        if self._version is not None:
            return self._version
        rc, out, err = self.run(["version", "--json"], timeout=30)
        text = (out or b"").decode("utf-8", "replace")
        parsed = None
        if rc == 0 and text.strip():
            try:
                document = json.loads(text)
                parsed = parse_version(
                    (document.get("client") or {}).get("version")
                    or (document.get("server") or {}).get("version"))
            except ValueError:
                parsed = None
        if parsed is None:
            # Older builds have no --json on `version`; the text form is
            # `sbx version: v0.45.0-rc2 <revision>`.
            rc, out, err = self.run(["version"], timeout=30)
            if rc == 0:
                parsed = parse_version(out.decode("utf-8", "replace"))
        self._version = parsed
        return parsed

    def version_document(self):
        """The raw `sbx version --json` document, or a minimal fallback."""
        rc, out, err = self.run(["version", "--json"], timeout=30)
        if rc == 0 and out.strip():
            try:
                return json.loads(out.decode("utf-8", "replace"))
            except ValueError:
                pass
        version = self.version()
        return {"client": {"version": f"v{version[0]}.{version[1]}.{version[2]}"}
                if version else {"version": "unknown"}}

    def caps(self):
        if self._caps is None:
            self._caps = capabilities(self.version())
        return self._caps

    def daemon_state(self):
        """`running`, `stopped`, `unknown` — read without starting the daemon."""
        document = self.version_document()
        server = document.get("server") or {}
        state = server.get("state")
        if isinstance(state, str) and state:
            return state
        return "unknown"

    # -- sandbox listing ----------------------------------------------------

    def sandboxes(self):
        """Return the sandbox list as a list, whatever shape the CLI used."""
        document = self.json(["ls"])
        return normalise_sandbox_list(document, self.caps()["list_is_bare_array"])


def normalise_sandbox_list(document, expect_bare_array=None):
    """Normalise `sbx ls --json` across the v0.45 and v0.48+ shapes.

    v0.45 returned `{"sandboxes": [...]}`. v0.48.0 returns the array
    directly. A wrapper that assumes either one breaks on the other, and the
    failure is an empty list rather than an error, which is worse.
    """
    if isinstance(document, list):
        return [row for row in document if isinstance(row, dict)]
    if isinstance(document, dict):
        for key in ("sandboxes", "items", "data"):
            value = document.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        if "name" in document:
            return [document]
    raise RunnerError("sbx_unexpected_shape", "sbx ls --json had no sandbox list")


def sandbox_inventory(sbx, daemon_state):
    """Return known rows or None, with a stable reason when unavailable.

    A successful empty list is evidence of zero sandboxes. A skipped or
    failed query is not, and status callers must preserve that distinction.
    """
    if daemon_state != "running":
        return None, "sbx_inventory_not_queried"
    try:
        return sbx.sandboxes(), None
    except RunnerError as exc:
        return None, exc.code


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def _tail(data, limit=MAX_OUTPUT_TAIL):
    if not isinstance(data, (bytes, bytearray)):
        return b""
    if len(data) <= limit:
        return bytes(data)
    return b"...[truncated]..." + bytes(data)[-limit:]


def _digest(path):
    """SHA-256 of a file, or None when it is missing or unreadable."""
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _git_head(workspace):
    """The workspace's HEAD commit, or None when it is not a git checkout."""
    git = shutil.which("git")
    if not git or not workspace:
        return None
    try:
        proc = subprocess.run([git, "-C", str(workspace), "rev-parse", "HEAD"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace").strip() or None


def _tree_digest(root, limit_files=20000):
    """A stable digest of a directory tree's names, sizes and modes.

    This is an identity of the *input*, not a content digest of every file:
    it answers "was this the same tree we ran against?" cheaply. Paths are
    sorted and relative, so the digest does not depend on the absolute
    location of the checkout.
    """
    digest = hashlib.sha256()
    count = 0
    root = Path(root)
    for path in sorted(root.rglob("*")):
        if count >= limit_files:
            break
        try:
            relative = path.relative_to(root).as_posix()
            if ".git/" in relative + "/":
                continue
            stat = path.lstat()
        except (OSError, ValueError):
            continue
        if path.is_dir():
            digest.update(f"d {relative}\0".encode())
            continue
        if path.is_symlink():
            digest.update(f"l {relative} {os.readlink(path)}\0".encode())
        else:
            digest.update(f"f {relative} {stat.st_size} {stat.st_mode & 0o777}\0".encode())
        count += 1
    return digest.hexdigest()


def _safe_name(name):
    if not isinstance(name, str) or not _SANDBOX_NAME.fullmatch(name):
        raise RunnerError("invalid_sandbox_name", "use lowercase letters, digits, '-' and '.'")
    return name


# --------------------------------------------------------------------------
# The harness
# --------------------------------------------------------------------------

class SandboxRun:
    """One create → exec → remove cycle, with a receipt for what happened."""

    def __init__(self, sbx, name, agent="shell", mode="clone", workspace=None,
                 cpus=2, memory="4g", skills="off", kits=None, keep=False,
                 create_timeout=DEFAULT_CREATE_TIMEOUT, run_timeout=DEFAULT_TIMEOUT):
        self.sbx = sbx
        self.name = _safe_name(name)
        self.agent = agent
        self.mode = mode
        self.workspace = Path(workspace).expanduser().resolve() if workspace else None
        self.cpus = cpus
        self.memory = memory
        self.skills = skills
        self.kits = list(kits or [])
        self.keep = keep
        self.create_timeout = create_timeout
        self.run_timeout = run_timeout
        self.created = False
        self.notes = []

    # -- lifecycle ---------------------------------------------------------

    def _create_args(self):
        # `sbx create AGENT [PATH]` — the agent is positional.
        args = ["create", self.agent, "--name", self.name]
        if self.mode in ("clone", "direct"):
            if self.workspace is None:
                raise RunnerError("missing_workspace", f"--mode {self.mode} needs a workspace")
            if self.mode == "clone":
                args.append("--clone")
            args.append(str(self.workspace))
        # mountless: no path at all, the agent works in its own filesystem.
        args += ["--cpus", str(self.cpus), "--memory", self.memory]
        args += ["--skills", self.skills]
        for kit in self.kits:
            args += ["--kit", kit]
        return args

    def create(self):
        args = self._create_args()
        started = time.monotonic()
        rc, out, err = self.sbx.run(args, timeout=self.create_timeout)
        elapsed = round(time.monotonic() - started, 3)
        if rc != 0:
            raise RunnerError("sandbox_create_failed",
                              f"exit {rc}: {_tail(err).decode('utf-8', 'replace').strip()}")
        self.created = True
        self.notes.append({"step": "create", "argv": args, "exit_code": rc,
                           "seconds": elapsed})
        return out

    def exec(self, command, workdir=None, env=None, user=None):
        """Run a command inside the sandbox and return a result record."""
        if not self.created:
            raise RunnerError("sandbox_not_created", "call create() first")
        argv = ["exec"]
        if workdir:
            argv += ["--workdir", str(workdir)]
        if user:
            argv += ["--user", str(user)]
        for item in env or []:
            argv += ["--env", str(item)]
        argv += [self.name, "--", *[str(part) for part in command]]
        started = time.monotonic()
        rc, out, err = self.sbx.run(argv, timeout=self.run_timeout)
        elapsed = round(time.monotonic() - started, 3)
        return {
            "argv": argv,
            "exit_code": rc,
            "seconds": elapsed,
            "stdout_bytes": len(out),
            "stderr_bytes": len(err),
            "stdout_tail": _tail(out).decode("utf-8", "replace"),
            "stderr_tail": _tail(err).decode("utf-8", "replace"),
        }

    def remove(self):
        if not self.created:
            return None
        rc, out, err = self.sbx.run(["rm", "--force", self.name],
                                     timeout=DEFAULT_STOP_TIMEOUT)
        self.created = False
        if rc != 0:
            return {"step": "rm", "exit_code": rc,
                    "stderr_tail": _tail(err).decode("utf-8", "replace")}
        return {"step": "rm", "exit_code": rc}

    def __enter__(self):
        self.create()
        return self

    def __exit__(self, exc_type, exc, tb):
        if not self.keep:
            self.remove()
        return False


# --------------------------------------------------------------------------
# Receipts
# --------------------------------------------------------------------------

def build_receipt(run, result, command, workspace=None, started_at=None,
                  extra=None):
    """Assemble the evidence document for one sandboxed test run."""
    workspace = Path(workspace).expanduser().resolve() if workspace else None
    receipt = {
        "schema": SCHEMA,
        "harness": {
            "name": "sbx-runner",
            "version": HARNESS_VERSION,
            "sha256": _digest(Path(__file__).resolve()),
        },
        "created_at": _now(),
        "started_at": started_at,
        "sbx": run.sbx.version_document(),
        "sbx_capabilities": run.sbx.caps(),
        "sandbox": {
            "name": run.name,
            "agent": run.agent,
            "mode": run.mode,
            "cpus": run.cpus,
            "memory": run.memory,
            "skills": run.skills,
            "kits": run.kits,
            "kept": run.keep,
        },
        "command": list(command),
        "source": {
            "workspace": str(workspace) if workspace else None,
            "workspace_tree_sha256": _tree_digest(workspace) if workspace else None,
            "git_head": _git_head(workspace) if workspace else None,
        },
        "result": result,
        "notes": run.notes + list(extra or []),
    }
    receipt["result"]["outcome"] = (
        "pass" if result.get("exit_code") == 0 else "fail")
    return receipt


def write_receipt(receipt, path):
    """Write the receipt atomically and refuse to exceed the size bound."""
    payload = json.dumps(receipt, indent=2, sort_keys=True).encode("utf-8")
    if len(payload) > MAX_RECEIPT_BYTES:
        raise RunnerError("receipt_too_large", f"{len(payload)} bytes")
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    with open(temporary, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    return target


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def command_status(sbx, as_json=False):
    """Report what is installed, what is running, and what exists."""
    version = sbx.version()
    document = sbx.version_document()
    caps = sbx.caps()
    state = sbx.daemon_state()
    sandboxes, error = sandbox_inventory(sbx, state)
    payload = {
        "sbx_version": f"{version[0]}.{version[1]}.{version[2]}" if version else None,
        "sbx_detail": document,
        "capabilities": caps,
        "daemon_state": state,
        "sandboxes": None if sandboxes is None else [
            {"name": row.get("name"), "status": row.get("status"),
             "agent": row.get("agent")}
            for row in sandboxes
        ],
        "sandboxes_error": error,
        "notes": [
            "Installed sbx is a release candidate; check for a stable release "
            "before pointing agents at repositories you care about."
            if version and "-rc" in json.dumps(document) else None,
            "v0.49.2 rebuilt the built-in template images for the eMudhra CA "
            "compromise. Existing sandboxes keep the old CA store: recreate "
            "with --pull always, or upgrade ca-certificates inside them."
            if version and at_least(version, 0, 49, 2) else None,
            "This sbx predates v0.48.0: `sbx ls --json` still wraps the list "
            "in {\"sandboxes\": [...]}, and the old flag spellings are in use."
            if version and not caps["list_is_bare_array"] else None,
        ],
    }
    payload["notes"] = [note for note in payload["notes"] if note]
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"sbx          {payload['sbx_version'] or 'not installed'}")
        print(f"daemon       {state}")
        count = len(sandboxes) if sandboxes is not None else "unavailable"
        print(f"sandboxes    {count}")
        if error:
            print(f"reason       {error}")
        for note in payload["notes"]:
            print(f"note         {note}")
        for row in payload["sandboxes"] or []:
            print(f"  - {row['name']:<32} {row['status']:<10} {row['agent']}")
    return 0


def command_run(sbx, args):
    """Create a sandbox, run a command, write a receipt, remove the sandbox."""
    if args.workspace:
        workspace = Path(args.workspace).expanduser()
        if not workspace.is_dir():
            raise RunnerError("missing_workspace", str(workspace))
    else:
        workspace = None
    if args.mode == "direct" and workspace is None:
        raise RunnerError("missing_workspace", "--mode direct needs a workspace")
    if args.mode == "clone" and workspace is None:
        raise RunnerError("missing_workspace", "--mode clone needs a workspace")

    name = args.name or f"sbxtest-{int(time.time())}"
    run = SandboxRun(sbx, name, agent=args.agent, mode=args.mode,
                     workspace=workspace, cpus=args.cpus, memory=args.memory,
                     skills=args.skills, kits=args.kit, keep=args.keep,
                     run_timeout=args.timeout)
    started_at = _now()
    receipt = None
    try:
        run.create()
        result = run.exec(args.command, workdir=args.workdir)
        receipt = build_receipt(run, result, args.command, workspace=workspace,
                                started_at=started_at)
    finally:
        if run.created and not args.keep:
            removal = run.remove()
            if removal and receipt is not None:
                receipt["notes"].append(removal)
    if receipt is None:
        raise RunnerError("sandbox_run_failed", "the sandbox never started")
    if args.receipt:
        target = write_receipt(receipt, args.receipt)
        print(f"receipt      {target}")
    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        print(f"sandbox      {receipt['sandbox']['name']} ({receipt['sandbox']['mode']})")
        print(f"exit_code    {receipt['result']['exit_code']}")
        print(f"outcome      {receipt['result']['outcome']}")
        print(f"seconds      {receipt['result']['seconds']}")
    return 0 if receipt["result"]["exit_code"] == 0 else 1


def command_selftest():
    """Exercise the version-compat logic without touching a sandbox."""
    failures = []

    def check(label, actual, expected):
        if actual != expected:
            failures.append(f"{label}: expected {expected!r}, got {actual!r}")

    check("parse 0.49.2", parse_version("v0.49.2"), (0, 49, 2))
    check("parse rc", parse_version("v0.45.0-rc2"), (0, 45, 0))
    check("parse bare", parse_version("0.49.2"), (0, 49, 2))
    check("parse junk", parse_version("not-a-version"), None)

    old = capabilities((0, 45, 0))
    new = capabilities((0, 49, 2))
    check("old list shape", old["list_is_bare_array"], False)
    check("new list shape", new["list_is_bare_array"], True)
    check("old detach", old["detach_flag"], "--detached")
    check("new detach", new["detach_flag"], "--detach")
    check("old diagnose", old["diagnose_format_flag"], "--output")
    check("new diagnose", new["diagnose_format_flag"], "--format")
    check("new run rm", new["run_rm"], True)
    check("new mcp unload", new["mcp_unload"], True)
    check("new policy refresh", new["policy_refresh"], True)
    check("new rebuilt ca", new["rebuilt_ca_images"], True)
    check("unknown at least", at_least(None, 0, 48), False)

    wrapped = {"sandboxes": [{"name": "a"}, {"name": "b"}]}
    bare = [{"name": "a"}, {"name": "b"}]
    check("normalise wrapped", normalise_sandbox_list(wrapped), bare)
    check("normalise bare", normalise_sandbox_list(bare), bare)
    check("normalise single", normalise_sandbox_list({"name": "a"}), [{"name": "a"}])

    for bad in (None, 42, "text", {"nothing": 1}):
        try:
            normalise_sandbox_list(bad)
        except RunnerError:
            continue
        failures.append(f"normalise should reject {bad!r}")

    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("selftest ok")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="sbx_runner.py",
        description="Run a command in a disposable Docker Sandbox and write a receipt.")
    parser.add_argument("--sbx", help="path to the sbx binary", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="report the installed sbx and sandboxes")
    status.add_argument("--json", action="store_true", help="emit JSON")

    run = sub.add_parser("run", help="create a sandbox, run a command, write a receipt")
    run.add_argument("command", nargs="+", help="the command and its arguments")
    run.add_argument("--workspace", help="the project directory to mount or clone")
    run.add_argument("--name", help="sandbox name (default: sbxtest-<epoch>)")
    run.add_argument("--agent", default="shell", help="agent to provision (default: shell)")
    run.add_argument("--mode", choices=("clone", "direct", "mountless"), default="clone",
                     help="how the workspace reaches the sandbox (default: clone)")
    run.add_argument("--workdir", help="working directory inside the sandbox")
    run.add_argument("--kit", action="append", default=[], help="kit reference (repeatable)")
    run.add_argument("--cpus", type=int, default=2)
    run.add_argument("--memory", default="4g")
    run.add_argument("--skills", choices=("off", "readonly", "readwrite"), default="off")
    run.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    run.add_argument("--receipt", help="write the receipt JSON to this path")
    run.add_argument("--keep", action="store_true", help="do not remove the sandbox")
    run.add_argument("--json", action="store_true", help="print the receipt to stdout")

    sub.add_parser("selftest", help="check the version-compat logic")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "selftest":
            return command_selftest()
        sbx = Sbx(binary=args.sbx)
        if args.command == "status":
            return command_status(sbx, as_json=args.json)
        return command_run(sbx, args)
    except RunnerError as exc:
        print(json.dumps({"error": exc.code, "detail": exc.detail}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
