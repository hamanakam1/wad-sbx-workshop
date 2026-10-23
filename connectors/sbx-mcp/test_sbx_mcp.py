#!/usr/bin/env python3
"""Tests for the sbx MCP connector.

The MCP surface is exercised in-process (no stdio, no sbx) except for one
test that drives the real transport with a pipe, so the framing is covered
too. `sbx` itself is never invoked: the read tools are stubbed.

Run with:  python3 connectors/sbx-mcp/test_sbx_mcp.py
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sbx_mcp as mcp  # noqa: E402
import sbx_runner as runner  # noqa: E402


def _request(method, request_id=1, **params):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("SBX_MCP_ALLOW", "SBX_MCP_ALLOWED_COMMANDS")}

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_read_tools_are_always_available(self):
        os.environ.pop("SBX_MCP_ALLOW", None)
        self.assertEqual(set(mcp.allowed_tools()), set(mcp.READ_TOOLS))

    def test_write_tools_need_an_explicit_grant(self):
        os.environ["SBX_MCP_ALLOW"] = "sbx_run_test"
        self.assertIn("sbx_run_test", mcp.allowed_tools())
        self.assertNotIn("sbx_collect_file", mcp.allowed_tools())

    def test_unknown_write_tool_is_not_granted_by_typo(self):
        os.environ["SBX_MCP_ALLOW"] = "sbx_run_testt"
        self.assertEqual(set(mcp.allowed_tools()), set(mcp.READ_TOOLS))

    def test_command_prefix_matching(self):
        os.environ["SBX_MCP_ALLOWED_COMMANDS"] = "npm test,python3 -m pytest"
        self.assertTrue(mcp.command_permitted(["npm", "test"]))
        self.assertTrue(mcp.command_permitted(["npm", "test", "--", "-k", "x"]))
        self.assertTrue(mcp.command_permitted(["python3", "-m", "pytest"]))
        self.assertFalse(mcp.command_permitted(["npm", "run", "test"]))
        self.assertFalse(mcp.command_permitted(["rm", "-rf", "/"]))
        self.assertFalse(mcp.command_permitted([]))

    def test_no_allowlist_means_nothing_is_permitted(self):
        os.environ.pop("SBX_MCP_ALLOWED_COMMANDS", None)
        self.assertFalse(mcp.command_permitted(["npm", "test"]))


class ProtocolTests(unittest.TestCase):
    def test_initialize_advertises_the_protocol(self):
        response = mcp.handle_request(_request("initialize"), {})
        self.assertEqual(response["result"]["protocolVersion"], mcp.PROTOCOL_VERSION)
        self.assertIn("tools", response["result"]["capabilities"])
        self.assertEqual(response["result"]["serverInfo"]["name"], mcp.SERVER_NAME)

    def test_notifications_return_nothing(self):
        self.assertIsNone(mcp.handle_request(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, {}))

    def test_tools_list_only_shows_enabled_tools(self):
        os.environ["SBX_MCP_ALLOW"] = ""
        response = mcp.handle_request(_request("tools/list"), {})
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertEqual(names, set(mcp.READ_TOOLS))

        os.environ["SBX_MCP_ALLOW"] = "sbx_collect_file"
        response = mcp.handle_request(_request("tools/list"), {})
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertIn("sbx_collect_file", names)
        self.assertNotIn("sbx_run_test", names)

    def test_unknown_tool_is_a_protocol_error(self):
        response = mcp.handle_request(_request("tools/call", name="nope"), {})
        self.assertIn("error", response)

    def test_disabled_tool_is_refused_with_the_reason(self):
        os.environ["SBX_MCP_ALLOW"] = ""
        response = mcp.handle_request(_request("tools/call", name="sbx_run_test"), {})
        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], -32601)
        self.assertEqual(response["error"]["data"]["required_env"], "SBX_MCP_ALLOW")

    def test_tool_failure_is_content_not_a_protocol_error(self):
        os.environ["SBX_MCP_ALLOW"] = "sbx_run_test"
        os.environ["SBX_MCP_ALLOWED_COMMANDS"] = "npm test"
        response = mcp.handle_request(
            _request("tools/call", name="sbx_run_test",
                     arguments={"command": ["rm", "-rf", "/"]}), {})
        self.assertIn("result", response)
        self.assertTrue(response["result"]["isError"])
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["error"], "command_not_allowed")
        self.assertIn("allowed_prefixes", payload["data"])

    def test_unknown_method_is_refused(self):
        response = mcp.handle_request(_request("does/not/exist"), {})
        self.assertEqual(response["error"]["code"], -32601)

    def test_ping(self):
        response = mcp.handle_request(_request("ping"), {})
        self.assertEqual(response["result"], {})

    def test_resources_and_prompts_are_empty(self):
        self.assertEqual(mcp.handle_request(_request("resources/list"), {})["result"]["resources"], [])
        self.assertEqual(mcp.handle_request(_request("prompts/list"), {})["result"]["prompts"], [])


class ToolArgumentTests(unittest.TestCase):
    def test_inspect_requires_a_name(self):
        with self.assertRaises(mcp.McpError):
            mcp.tool_sbx_inspect_sandbox({})
        with self.assertRaises(mcp.McpError):
            mcp.tool_sbx_inspect_sandbox({"name": "  "})

    def test_policy_check_requires_a_host(self):
        with self.assertRaises(mcp.McpError):
            mcp.tool_sbx_policy_check({})

    def test_run_test_requires_a_command_list(self):
        os.environ["SBX_MCP_ALLOW"] = "sbx_run_test"
        os.environ["SBX_MCP_ALLOWED_COMMANDS"] = "npm test"
        for bad in ({}, {"command": "npm test"}, {"command": []},
                    {"command": ["npm", 3]}):
            with self.assertRaises(mcp.McpError):
                mcp.tool_sbx_run_test(bad)

    def test_run_test_requires_a_workspace_for_clone_mode(self):
        os.environ["SBX_MCP_ALLOW"] = "sbx_run_test"
        os.environ["SBX_MCP_ALLOWED_COMMANDS"] = "npm test"
        with self.assertRaises(mcp.McpError):
            mcp.tool_sbx_run_test({"command": ["npm", "test"], "mode": "clone"})

    def test_run_test_rejects_an_unknown_mode(self):
        os.environ["SBX_MCP_ALLOW"] = "sbx_run_test"
        os.environ["SBX_MCP_ALLOWED_COMMANDS"] = "npm test"
        with self.assertRaises(mcp.McpError):
            mcp.tool_sbx_run_test({"command": ["npm", "test"], "mode": "sideload"})

    def test_collect_requires_all_three_fields(self):
        for bad in ({}, {"sandbox": "a"}, {"sandbox": "a", "remote_path": "/b"}):
            with self.assertRaises(mcp.McpError):
                mcp.tool_sbx_collect_file(bad)


class TransportTests(unittest.TestCase):
    def test_newline_delimited_framing(self):
        lines = [json.dumps(_request("initialize", 1)),
                 json.dumps(_request("tools/list", 2)),
                 json.dumps(_request("ping", 3))]
        stdout = io.StringIO()
        mcp.serve(io.StringIO("\n".join(lines) + "\n"), stdout)
        responses = [json.loads(line) for line in stdout.getvalue().splitlines() if line]
        self.assertEqual(len(responses), 3)
        self.assertEqual([r["id"] for r in responses], [1, 2, 3])

    def test_a_parse_error_does_not_stop_the_server(self):
        stdout = io.StringIO()
        mcp.serve(io.StringIO("{not json\n" + json.dumps(_request("ping", 9)) + "\n"), stdout)
        responses = [json.loads(line) for line in stdout.getvalue().splitlines() if line]
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1]["id"], 9)

    def test_blank_lines_are_ignored(self):
        stdout = io.StringIO()
        mcp.serve(io.StringIO("\n\n" + json.dumps(_request("ping", 4)) + "\n\n"), stdout)
        responses = [json.loads(line) for line in stdout.getvalue().splitlines() if line]
        self.assertEqual(len(responses), 1)


class StatusToolTests(unittest.TestCase):
    def _status(self, state, rows=None, error=None):
        sbx = mock.Mock(spec=runner.Sbx)
        sbx.version.return_value = (0, 49, 2)
        sbx.daemon_state.return_value = state
        sbx.caps.return_value = runner.capabilities((0, 49, 2))
        sbx.sandboxes.return_value = rows if rows is not None else []
        sbx.sandboxes.side_effect = error
        with mock.patch.object(mcp, "_sandbox", return_value=sbx):
            response = mcp.handle_request(
                _request("tools/call", name="sbx_status", arguments={}), {})
        self.assertNotIn("error", response)
        self.assertFalse(response["result"]["isError"])
        payload = response["result"]["structuredContent"]
        self.assertEqual(json.loads(response["result"]["content"][0]["text"]), payload)
        return sbx, payload

    def test_successful_empty_inventory_reports_zero(self):
        sbx, payload = self._status("running")
        sbx.sandboxes.assert_called_once_with()
        self.assertEqual(payload["sandbox_count"], 0)
        self.assertIsNone(payload.get("sandboxes_error"))

    def test_successful_inventory_reports_the_exact_count(self):
        sbx, payload = self._status("running", rows=[{"name": "fixture-1"}, {"name": "fixture-2"}])
        sbx.sandboxes.assert_called_once_with()
        self.assertEqual(payload["sandbox_count"], 2)
        self.assertIsNone(payload.get("sandboxes_error"))

    def test_failed_inventory_reports_null_and_the_original_error_code(self):
        for code in ("sbx_timeout", "sbx_command_failed"):
            with self.subTest(code=code):
                sbx, payload = self._status(
                    "running", error=runner.RunnerError(code, "synthetic private diagnostic"))
                sbx.sandboxes.assert_called_once_with()
                self.assertIsNone(payload["sandbox_count"])
                self.assertEqual(payload["sandboxes_error"], code)
                self.assertNotIn("synthetic private diagnostic", str(payload))

    def _assert_inventory_not_queried(self, state):
        sbx, payload = self._status(state)
        sbx.sandboxes.assert_not_called()
        self.assertEqual(payload["daemon_state"], state)
        self.assertIsNone(payload["sandbox_count"])
        self.assertEqual(payload["sandboxes_error"], "sbx_inventory_not_queried")

    def test_stopped_daemon_does_not_query_inventory(self):
        self._assert_inventory_not_queried("stopped")

    def test_unknown_daemon_does_not_query_inventory(self):
        self._assert_inventory_not_queried("unknown")


class RunnerIntegrationTests(unittest.TestCase):
    """The connector must not drift from the runner it depends on."""

    def test_runner_and_connector_agree_on_the_schema(self):
        self.assertTrue(hasattr(runner, "SCHEMA"))
        self.assertEqual(runner.SCHEMA, "sbx-sandbox-test-receipt-v1")

    def test_run_test_clones_the_requested_workspace(self):
        sbx = runner.Sbx(binary="/fake/sbx")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "project with spaces"
            workspace.mkdir()
            with mock.patch.dict(os.environ, {"SBX_MCP_ALLOWED_COMMANDS": "npm test"}), \
                    mock.patch.object(mcp, "_sandbox", return_value=sbx), \
                    mock.patch.object(sbx, "run", return_value=(0, b"", b"")) as run, \
                    mock.patch.object(sbx, "run_exec", return_value=runner.ExecCapture(0, 0, 0, b"", b"")) as execute, \
                    mock.patch.object(runner, "build_receipt", wraps=runner.build_receipt) as build:
                result = mcp.tool_sbx_run_test({
                    "command": ["npm", "test"], "workspace": str(workspace)})
            create_call = run.call_args_list[0].args[0]
            self.assertIn("--clone", create_call)
            self.assertIn(str(workspace.resolve()), create_call)
            self.assertEqual(build.call_args.kwargs["workspace"], str(workspace))
            execute.assert_called_once()
            self.assertEqual(execute.call_args.args[0][-3:], ["--", "npm", "test"])
            self.assertEqual(run.call_args_list[-1].args[0][0], "rm")
            self.assertEqual(result["outcome"], "pass")

    def test_status_tool_reports_the_runner_capabilities(self):
        class FakeSbx(runner.Sbx):
            def __init__(self):
                self.binary = "/fake/sbx"
                self.timeout = 5
                self._version = (0, 48, 0)
                self._caps = None

            def version_document(self):
                return {"client": {"version": "v0.48.0"}}

            def daemon_state(self):
                return "stopped"

        original = mcp._sandbox
        mcp._sandbox = lambda: FakeSbx()
        try:
            payload = mcp.tool_sbx_status({})
        finally:
            mcp._sandbox = original
        self.assertEqual(payload["sbx_version"], "0.48.0")
        self.assertEqual(payload["daemon_state"], "stopped")
        self.assertTrue(payload["capabilities"]["list_is_bare_array"])
        # v0.47.0 added `sbx run --rm`, so a 0.48.0 build has it.
        self.assertTrue(payload["capabilities"]["run_rm"])
        # v0.49.1 added `sbx policy refresh`; a 0.48.0 build does not.
        self.assertFalse(payload["capabilities"]["policy_refresh"])

    def test_tool_definitions_are_well_formed(self):
        for tool in mcp.tool_definitions():
            self.assertIn("name", tool)
            self.assertIn("description", tool)
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertIn("annotations", tool)
            self.assertIn("readOnlyHint", tool["annotations"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
