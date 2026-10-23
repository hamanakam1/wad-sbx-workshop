#!/usr/bin/env python3
"""Tests for the sandbox runner.

Nothing here touches a real sandbox. Fake sbx responses exercise argument
construction, version compatibility and receipt assembly. Synthetic Python
children exercise real exec pipes, memory bounds and timeout cleanup without
a daemon, a login or a hypervisor.

Run with:  python3 connectors/sbx-runner/test_sbx_runner.py
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sbx_runner as runner  # noqa: E402


class FakeSbx(runner.Sbx):
    """A scripted sbx. Each entry maps a command prefix to a response."""

    def __init__(self, responses=None, version="v0.49.2"):
        self.binary = "/fake/sbx"
        self.timeout = 30
        self._version = None
        self._caps = None
        self.responses = responses or {}
        self.calls = []

    def run(self, args, timeout=None, stdin=None):
        argv = [str(a) for a in args]
        self.calls.append(argv)
        for prefix, response in self.responses.items():
            wanted = prefix.split() if isinstance(prefix, str) else [str(p) for p in prefix]
            if argv[:len(wanted)] == wanted:
                if callable(response):
                    return response(argv)
                return response
        return 0, b"", b""

    def run_exec(self, args, timeout=None):
        rc, out, err = self.run(args, timeout=timeout)
        return runner.ExecCapture(rc, len(out), len(err), runner._tail(out), runner._tail(err))


class VersionTests(unittest.TestCase):
    def test_parse_version_shapes(self):
        self.assertEqual(runner.parse_version("v0.49.2"), (0, 49, 2))
        self.assertEqual(runner.parse_version("v0.45.0-rc2"), (0, 45, 0))
        self.assertEqual(runner.parse_version("0.49.2"), (0, 49, 2))
        self.assertEqual(runner.parse_version("sbx version: v1.2.3 (abc)"), (1, 2, 3))
        self.assertIsNone(runner.parse_version("unknown"))
        self.assertIsNone(runner.parse_version(None))
        self.assertIsNone(runner.parse_version(12345))

    def test_at_least(self):
        self.assertTrue(runner.at_least((0, 49, 2), 0, 48))
        self.assertTrue(runner.at_least((0, 48, 0), 0, 48))
        self.assertFalse(runner.at_least((0, 47, 9), 0, 48))
        self.assertFalse(runner.at_least(None, 0, 1))

    def test_capability_boundaries(self):
        old = runner.capabilities((0, 45, 0))
        self.assertEqual(old["detach_flag"], "--detached")
        self.assertEqual(old["no_progress_flag"], "--quiet")
        self.assertEqual(old["diagnose_format_flag"], "--output")
        self.assertFalse(old["list_is_bare_array"])
        self.assertFalse(old["run_rm"])
        self.assertFalse(old["mcp_unload"])
        self.assertFalse(old["policy_refresh"])
        self.assertFalse(old["kit_validate_offline"])
        self.assertFalse(old["rebuilt_ca_images"])

        new = runner.capabilities((0, 49, 2))
        self.assertEqual(new["detach_flag"], "--detach")
        self.assertEqual(new["no_progress_flag"], "--no-progress")
        self.assertEqual(new["diagnose_format_flag"], "--format")
        self.assertTrue(new["list_is_bare_array"])
        self.assertTrue(new["run_rm"])
        self.assertTrue(new["mcp_unload"])
        self.assertTrue(new["policy_refresh"])
        self.assertTrue(new["kit_validate_offline"])
        self.assertTrue(new["rebuilt_ca_images"])

    def test_capabilities_of_unknown_version_are_conservative(self):
        caps = runner.capabilities(None)
        self.assertFalse(caps["list_is_bare_array"])
        self.assertFalse(caps["run_rm"])
        self.assertEqual(caps["detach_flag"], "--detached")


class ShapeTests(unittest.TestCase):
    def test_wrapped_shape(self):
        document = {"sandboxes": [{"name": "a"}, {"name": "b"}]}
        self.assertEqual(runner.normalise_sandbox_list(document),
                         [{"name": "a"}, {"name": "b"}])

    def test_bare_shape(self):
        document = [{"name": "a"}, {"name": "b"}]
        self.assertEqual(runner.normalise_sandbox_list(document),
                         [{"name": "a"}, {"name": "b"}])

    def test_single_object(self):
        self.assertEqual(runner.normalise_sandbox_list({"name": "a"}), [{"name": "a"}])

    def test_non_dict_rows_are_dropped(self):
        self.assertEqual(runner.normalise_sandbox_list([{"name": "a"}, "junk", 3]),
                         [{"name": "a"}])

    def test_unusable_shapes_raise(self):
        for bad in (None, 42, "text", {"nothing": 1}, {"sandboxes": "nope"}):
            with self.assertRaises(runner.RunnerError):
                runner.normalise_sandbox_list(bad)

    def test_error_carries_a_stable_code(self):
        try:
            runner.normalise_sandbox_list(None)
        except runner.RunnerError as exc:
            self.assertEqual(exc.code, "sbx_unexpected_shape")
        else:
            self.fail("expected RunnerError")


class SandboxArgumentTests(unittest.TestCase):
    def _run(self, **kwargs):
        sbx = FakeSbx()
        run = runner.SandboxRun(sbx, "sbxtest-1", **kwargs)
        return run, sbx

    def test_clone_mode_passes_the_requested_workspace(self):
        run, sbx = self._run(mode="clone", workspace="/tmp/project")
        args = run._create_args()
        self.assertEqual(args[0], "create")
        self.assertEqual(args[1], "shell")
        self.assertIn("--clone", args)
        self.assertIn(str(Path("/tmp/project").resolve()), args)

    def test_direct_mode_passes_the_workspace_path(self):
        run, sbx = self._run(mode="direct", workspace="/tmp/project")
        args = run._create_args()
        self.assertNotIn("--clone", args)
        # The runner resolves the workspace, so compare against the resolved
        # form: on macOS /tmp is a symlink to /private/tmp.
        self.assertIn(str(Path("/tmp/project").resolve()), args)

    def test_mountless_mode_passes_no_path(self):
        run, sbx = self._run(mode="mountless")
        args = run._create_args()
        self.assertNotIn("--clone", args)
        # create, agent, --name, name, --cpus, n, --memory, m, --skills, off
        self.assertEqual(len(args), 10)
        self.assertEqual(args[0], "create")
        self.assertNotIn("--clone", args)

    def test_direct_mode_without_workspace_is_refused(self):
        run, sbx = self._run(mode="direct", workspace=None)
        with self.assertRaises(runner.RunnerError):
            run._create_args()

    def test_clone_mode_without_workspace_never_calls_sbx(self):
        run, sbx = self._run(mode="clone")
        with self.assertRaises(runner.RunnerError) as raised:
            run.create()
        self.assertEqual(raised.exception.code, "missing_workspace")
        self.assertEqual(sbx.calls, [])

    def test_kits_are_repeatable(self):
        run, sbx = self._run(mode="mountless", kits=["a", "b"])
        args = run._create_args()
        self.assertEqual(args.count("--kit"), 2)
        self.assertIn("a", args)
        self.assertIn("b", args)

    def test_invalid_sandbox_names_are_refused(self):
        for bad in ("", "A", "has space", "x" * 80, "UPPER", "semi;colon"):
            with self.assertRaises(runner.RunnerError):
                runner.SandboxRun(FakeSbx(), bad)

    def test_exec_argv_uses_the_separator(self):
        run, sbx = self._run(mode="mountless")
        run.created = True
        run.exec(["npm", "test"], workdir="/w", env=["A=1"])
        argv = sbx.calls[-1]
        self.assertEqual(argv[:2], ["exec", "--workdir"])
        self.assertIn("--", argv)
        self.assertEqual(argv[argv.index("--") + 1:], ["npm", "test"])
        self.assertIn("--env", argv)


class CommandRunTests(unittest.TestCase):
    def test_clone_workspace_matches_receipt_when_invoked_elsewhere(self):
        sbx = FakeSbx()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "project with spaces"
            workspace.mkdir()
            receipt_path = Path(directory) / "receipt.json"
            self.assertNotEqual(Path.cwd(), workspace.resolve())
            with mock.patch.object(runner, "Sbx", return_value=sbx), \
                    mock.patch("sys.stdout", new_callable=io.StringIO):
                status = runner.main([
                    "run", "--workspace", str(workspace),
                    "--receipt", str(receipt_path), "--", "npm", "test"])
            receipt = json.loads(receipt_path.read_text())
        self.assertEqual(status, 0)
        create_call = next(call for call in sbx.calls if call[0] == "create")
        self.assertIn("--clone", create_call)
        self.assertIn(str(workspace.resolve()), create_call)
        self.assertEqual(receipt["source"]["workspace"], str(workspace.resolve()))
        self.assertEqual(receipt["notes"][0]["argv"], create_call)
        self.assertEqual(receipt["command"], ["npm", "test"])
        self.assertEqual(receipt["notes"][-1]["step"], "rm")


class ExecTests(unittest.TestCase):
    def test_exec_reports_exit_code_and_output(self):
        sbx = FakeSbx({"exec": (3, b"out", b"err")})
        run = runner.SandboxRun(sbx, "sbxtest-1", mode="mountless")
        run.created = True
        result = run.exec(["false"])
        self.assertEqual(result["exit_code"], 3)
        self.assertEqual(result["stdout_tail"], "out")
        self.assertEqual(result["stderr_tail"], "err")
        self.assertEqual(result["stdout_bytes"], 3)

    def test_exec_before_create_is_refused(self):
        sbx = FakeSbx()
        run = runner.SandboxRun(sbx, "sbxtest-1", mode="mountless")
        with self.assertRaises(runner.RunnerError):
            run.exec(["true"])

    def test_output_is_truncated_not_stored_whole(self):
        blob = b"x" * (runner.MAX_CAPTURE_BYTES + 5000)
        sbx = FakeSbx({"exec": (0, blob, b"")})
        run = runner.SandboxRun(sbx, "sbxtest-1", mode="mountless")
        run.created = True
        result = run.exec(["true"])
        self.assertLess(len(result["stdout_tail"]), runner.MAX_OUTPUT_TAIL + 100)
        self.assertIn("truncated", result["stdout_tail"])
        self.assertEqual(result["stdout_bytes"], len(blob))


class ExecProcessTests(unittest.TestCase):
    """Exercise real pipes with synthetic Python children, never sbx."""

    def _run(self, program, timeout=10):
        class PythonChildSbx(runner.Sbx):
            def _argv(self, args):
                return [sys.executable, "-u", "-c", program]

        run = runner.SandboxRun(PythonChildSbx(binary=sys.executable),
                                "synthetic-test", mode="mountless", run_timeout=timeout)
        run.created = True
        return run

    @unittest.skipUnless(os.name == "posix", "bounded pipe capture requires Unix")
    def test_large_concurrent_streams_keep_exact_counts_and_tails_with_bounded_memory(self):
        program = """
import os
import threading

def emit(fd, byte, ending):
    for _ in range(128):
        block = byte * 65536
        while block:
            block = block[os.write(fd, block):]
    os.write(fd, ending)

threads = [threading.Thread(target=emit, args=(1, b'o', b'stdout-end\\n')),
           threading.Thread(target=emit, args=(2, b'e', b'stderr-end\\n'))]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
raise SystemExit(7)
"""
        run = self._run(program)
        tracemalloc.start()
        try:
            result = run.exec(["synthetic-command"])
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(result["exit_code"], 7)
        for stream, fill in (("stdout", "o"), ("stderr", "e")):
            ending = f"{stream}-end\n"
            self.assertEqual(result[f"{stream}_bytes"], 8 * 1024 * 1024 + len(ending))
            self.assertEqual(result[f"{stream}_tail"],
                             "...[truncated]..." + fill * (runner.MAX_OUTPUT_TAIL - len(ending)) + ending)
        self.assertLess(peak, 4 * 1024 * 1024,
                        f"exec capture retained too much parent memory: {peak} bytes")

    def test_short_output_preserves_bytes_and_replacement_decoding(self):
        run = self._run("import os; os.write(1, b'out\\xff'); os.write(2, b'err\\xfe')")
        result = run.exec(["synthetic-command"])
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout_bytes"], 4)
        self.assertEqual(result["stderr_bytes"], 4)
        self.assertEqual(result["stdout_tail"], "out\ufffd")
        self.assertEqual(result["stderr_tail"], "err\ufffd")

    def test_windows_uses_existing_capture_with_exact_counts_and_tails(self):
        sbx = runner.Sbx(binary="/fake/sbx")
        args = ["exec", "synthetic-test", "--", "false"]
        out = b"x" * (runner.MAX_OUTPUT_TAIL + 3) + b"\xff"
        err = b"err\xfe"
        with mock.patch.object(runner.os, "name", "nt"), \
                mock.patch.object(sbx, "run", return_value=(9, out, err)) as legacy_run, \
                mock.patch.object(runner.selectors, "DefaultSelector",
                                  side_effect=AssertionError("Windows pipes are not selectable")) as selector:
            result = sbx.run_exec(args, timeout=23)
        legacy_run.assert_called_once_with(args, timeout=23)
        selector.assert_not_called()
        self.assertEqual(result.exit_code, 9)
        self.assertEqual(result.stdout_bytes, len(out))
        self.assertEqual(result.stderr_bytes, len(err))
        self.assertEqual(result.stdout_tail,
                         b"...[truncated]..." + b"x" * (runner.MAX_OUTPUT_TAIL - 1) + b"\xff")
        self.assertEqual(result.stderr_tail, err)

    def _assert_timeout_reaps_child(self, program):
        processes = []
        original_popen = runner.subprocess.Popen

        def tracked_popen(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            processes.append(process)
            return process

        started = time.monotonic()
        with mock.patch.object(runner.subprocess, "Popen", side_effect=tracked_popen):
            with self.assertRaises(runner.RunnerError) as raised:
                self._run(program, timeout=0.3).exec(["synthetic-command"])
        self.assertEqual(raised.exception.code, "sbx_timeout")
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(len(processes), 1)
        process = processes[0]
        self.assertIsNotNone(process.returncode)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        with self.assertRaises(ChildProcessError):
            os.waitpid(process.pid, os.WNOHANG)

    @unittest.skipUnless(os.name == "posix", "child reaping check requires Unix waitpid")
    def test_timeout_kills_and_reaps_the_owned_child(self):
        self._assert_timeout_reaps_child("import time; print('ready', flush=True); time.sleep(10)")

    @unittest.skipUnless(os.name == "posix", "child reaping check requires Unix waitpid")
    def test_timeout_still_applies_after_both_output_pipes_close(self):
        self._assert_timeout_reaps_child("import os, time; os.close(1); os.close(2); time.sleep(10)")


class ReceiptTests(unittest.TestCase):
    def test_receipt_has_the_expected_shape(self):
        sbx = FakeSbx()
        run = runner.SandboxRun(sbx, "sbxtest-1", mode="clone", workspace=None)
        run.created = True
        run.notes.append({"step": "create", "exit_code": 0})
        result = {"exit_code": 0, "seconds": 1.5, "stdout_tail": "", "stderr_tail": ""}
        receipt = runner.build_receipt(run, result, ["npm", "test"])
        self.assertEqual(receipt["schema"], runner.SCHEMA)
        self.assertEqual(receipt["result"]["outcome"], "pass")
        self.assertEqual(receipt["command"], ["npm", "test"])
        self.assertEqual(receipt["sandbox"]["mode"], "clone")
        self.assertEqual(receipt["harness"]["name"], "sbx-runner")
        self.assertRegex(receipt["harness"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertIn("started_at", receipt)
        self.assertIn("list_is_bare_array", receipt["sbx_capabilities"])

    def test_failure_is_reported_as_fail(self):
        sbx = FakeSbx()
        run = runner.SandboxRun(sbx, "sbxtest-1", mode="mountless")
        run.created = True
        receipt = runner.build_receipt(run, {"exit_code": 1}, ["false"])
        self.assertEqual(receipt["result"]["outcome"], "fail")

    def test_receipt_round_trips_through_a_file(self):
        sbx = FakeSbx()
        run = runner.SandboxRun(sbx, "sbxtest-1", mode="mountless")
        run.created = True
        receipt = runner.build_receipt(run, {"exit_code": 0}, ["true"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "receipt.json"
            runner.write_receipt(receipt, path)
            loaded = json.loads(path.read_text())
        self.assertEqual(loaded["schema"], runner.SCHEMA)
        self.assertEqual(loaded["command"], ["true"])

    def test_oversized_receipt_is_refused(self):
        sbx = FakeSbx()
        run = runner.SandboxRun(sbx, "sbxtest-1", mode="mountless")
        run.created = True
        receipt = runner.build_receipt(run, {"exit_code": 0}, ["true"])
        receipt["result"]["stdout_tail"] = "x" * (runner.MAX_RECEIPT_BYTES + 1)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(runner.RunnerError):
                runner.write_receipt(receipt, Path(directory) / "r.json")


class TreeDigestTests(unittest.TestCase):
    def test_digest_is_stable_and_path_independent(self):
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            for root in (one, two):
                (Path(root) / "a.txt").write_text("hello")
                (Path(root) / "sub").mkdir()
                (Path(root) / "sub" / "b.txt").write_text("world")
            self.assertEqual(runner._tree_digest(one), runner._tree_digest(two))

    def test_digest_changes_with_content(self):
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            (Path(one) / "a.txt").write_text("hello")
            (Path(two) / "a.txt").write_text("hello!")
            self.assertNotEqual(runner._tree_digest(one), runner._tree_digest(two))

    def test_git_directory_is_excluded(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "a.txt").write_text("hello")
            (Path(root) / ".git").mkdir()
            (Path(root) / ".git" / "HEAD").write_text("ref: refs/heads/main")
            digest = runner._tree_digest(root)
        with tempfile.TemporaryDirectory() as other:
            (Path(other) / "a.txt").write_text("hello")
            self.assertEqual(digest, runner._tree_digest(other))


class StatusCommandTests(unittest.TestCase):
    def _status(self, state, rows=None, error=None, as_json=False):
        sbx = mock.Mock(spec=runner.Sbx)
        sbx.version.return_value = (0, 49, 2)
        sbx.version_document.return_value = {
            "client": {"version": "v0.49.2"}, "server": {"state": state}}
        sbx.caps.return_value = runner.capabilities((0, 49, 2))
        sbx.daemon_state.return_value = state
        sbx.sandboxes.return_value = rows if rows is not None else []
        sbx.sandboxes.side_effect = error
        with mock.patch.object(runner, "Sbx", return_value=sbx), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            status = runner.main(["status", *(["--json"] if as_json else [])])
        self.assertEqual(status, 0)
        return sbx, json.loads(output.getvalue()) if as_json else output.getvalue()

    def test_successful_empty_inventory_is_confirmed_empty(self):
        for as_json in (False, True):
            with self.subTest(as_json=as_json):
                sbx, report = self._status("running", as_json=as_json)
                sbx.sandboxes.assert_called_once_with()
                if as_json:
                    self.assertEqual(report["sandboxes"], [])
                    self.assertIsNone(report["sandboxes_error"])
                else:
                    self.assertIn("sandboxes    0\n", report)
                    self.assertNotIn("unavailable", report)

    def test_successful_inventory_reports_the_returned_rows(self):
        rows = [{"name": "fixture-1", "status": "running", "agent": "shell"},
                {"name": "fixture-2", "status": "stopped", "agent": "shell"}]
        for as_json in (False, True):
            with self.subTest(as_json=as_json):
                sbx, report = self._status("running", rows=rows, as_json=as_json)
                sbx.sandboxes.assert_called_once_with()
                if as_json:
                    self.assertEqual(report["sandboxes"], rows)
                    self.assertIsNone(report["sandboxes_error"])
                else:
                    self.assertIn("sandboxes    2\n", report)
                    self.assertIn("fixture-1", report)
                    self.assertIn("fixture-2", report)

    def test_failed_inventory_is_unavailable_with_the_original_error_code(self):
        for code in ("sbx_timeout", "sbx_command_failed"):
            for as_json in (False, True):
                with self.subTest(code=code, as_json=as_json):
                    sbx, report = self._status(
                        "running", error=runner.RunnerError(code, "synthetic private diagnostic"),
                        as_json=as_json)
                    sbx.sandboxes.assert_called_once_with()
                    if as_json:
                        self.assertIsNone(report["sandboxes"])
                        self.assertEqual(report["sandboxes_error"], code)
                    else:
                        self.assertIn("sandboxes    unavailable\n", report)
                        self.assertIn(code, report)
                        self.assertNotIn("sandboxes    0\n", report)
                    self.assertNotIn("synthetic private diagnostic", str(report))

    def _assert_inventory_not_queried(self, state):
        for as_json in (False, True):
            with self.subTest(as_json=as_json):
                sbx, report = self._status(state, as_json=as_json)
                sbx.sandboxes.assert_not_called()
                if as_json:
                    self.assertEqual(report["daemon_state"], state)
                    self.assertIsNone(report["sandboxes"])
                    self.assertEqual(report["sandboxes_error"], "sbx_inventory_not_queried")
                else:
                    self.assertIn("sandboxes    unavailable\n", report)
                    self.assertIn("sbx_inventory_not_queried", report)
                    self.assertNotIn("sandboxes    0\n", report)

    def test_stopped_daemon_does_not_query_inventory(self):
        self._assert_inventory_not_queried("stopped")

    def test_unknown_daemon_does_not_query_inventory(self):
        self._assert_inventory_not_queried("unknown")


class SelftestCommandTests(unittest.TestCase):
    def test_selftest_passes(self):
        buffer = io.StringIO()
        original = sys.stdout
        sys.stdout = buffer
        try:
            code = runner.command_selftest()
        finally:
            sys.stdout = original
        self.assertEqual(code, 0)
        self.assertIn("selftest ok", buffer.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
