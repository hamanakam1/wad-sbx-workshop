#!/usr/bin/env python3
"""An MCP stdio server that exposes Docker Sandboxes to an agent.

Why this exists
---------------
The `sbx` CLI is a host tool. An agent inside a sandbox cannot reach it: the
sandbox has no host filesystem and no route to the host's binaries. The
workshop's own answer is the SBX MCP gateway — you register a host stdio
server with `sbx mcp add` and the agent gets named tools instead of a mount.
This server is one of those: register it once on the host, and a sandboxed
agent can ask what sandboxes exist, read a sandbox's state, and (only when
you allow it) run a command inside a disposable sandbox and collect a
receipt.

The interesting part is not the proxy. It is that the write path is
*structurally* separated from the read path:

  * Read tools (`sbx_status`, `sbx_list_sandboxes`, `sbx_inspect_sandbox`,
    `sbx_policy_check`) are always available and can only read.
  * Write tools (`sbx_run_test`, `sbx_collect_file`) are refused unless
    they are named in `SBX_MCP_ALLOW`, and `sbx_run_test` additionally
    requires its command to match `SBX_MCP_ALLOWED_COMMANDS`. A sandbox that
    reaches this server without the environment set gets reads and an
    explicit refusal with the reason, not a silent success.

The refusal is the point. "The tool is there but it says no and says why"
is a different thing from "the tool is not there", and the first one can be
reviewed.

Transport: newline-delimited JSON-RPC 2.0 on stdio, which is what MCP's
stdio transport uses. One JSON object per line, in both directions.

    register on the host:
      sbx mcp add sbx --command python3 --args "/path/to/sbx_mcp.py"

    give a sandbox the tools (static mode):
      sbx run claude . --static-mcp sbx
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
# The runner lives next door; the connector reuses its version-compat shims
# and its receipt builder so the two never disagree about the sbx surface.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sbx-runner"))

import sbx_runner as runner  # noqa: E402

PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "sbx-connectors"
SERVER_VERSION = "1.0.0"

READ_TOOLS = ("sbx_status", "sbx_list_sandboxes", "sbx_inspect_sandbox",
              "sbx_policy_check")
WRITE_TOOLS = ("sbx_run_test", "sbx_collect_file")


class McpError(Exception):
    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}


# --------------------------------------------------------------------------
# Policy for this process
# --------------------------------------------------------------------------

def _env_list(name):
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def allowed_tools():
    """The write tools this process may expose. Reads are always on."""
    granted = set(_env_list("SBX_MCP_ALLOW"))
    return READ_TOOLS + tuple(t for t in WRITE_TOOLS if t in granted)


def allowed_commands():
    """Command prefixes the write path may run. Empty means none."""
    return _env_list("SBX_MCP_ALLOWED_COMMANDS")


def command_permitted(argv):
    """True when `argv` matches an allow-listed command prefix.

    The prefix must cover the *program*, not just a substring: `npm test`
    allows `npm test -- --watch`, and does not allow `rm`.
    """
    prefixes = allowed_commands()
    if not prefixes:
        return False
    if not argv:
        return False
    for prefix in prefixes:
        parts = prefix.split()
        if argv[:len(parts)] == parts:
            return True
    return False


# --------------------------------------------------------------------------
# Tool descriptions
# --------------------------------------------------------------------------

def tool_definitions():
    def tool(name, description, schema, read_only=True, destructive=False):
        return {
            "name": name,
            "description": description,
            "inputSchema": schema,
            "annotations": {
                "readOnlyHint": read_only,
                "destructiveHint": destructive,
                "idempotentHint": read_only,
                "openWorldHint": False,
            },
        }

    return [
        tool("sbx_status",
             "Report the installed Docker Sandboxes CLI version, the daemon state, "
             "and the sandbox count when available, or why it is unavailable. Read-only.",
             {"type": "object", "properties": {}, "additionalProperties": False}),
        tool("sbx_list_sandboxes",
             "List the sandboxes on this host with their agent, status and "
             "workspace. Read-only.",
             {"type": "object", "properties": {}, "additionalProperties": False}),
        tool("sbx_inspect_sandbox",
             "Show the full inspection record for one sandbox: its kits, "
             "mounts, network policy, secrets and ports. Read-only.",
             {"type": "object",
              "properties": {"name": {"type": "string",
                                      "description": "The sandbox name."}},
              "required": ["name"], "additionalProperties": False}),
        tool("sbx_policy_check",
             "Ask the network policy whether a destination would be allowed, "
             "and whether an explicit rule exists. Read-only.",
             {"type": "object",
              "properties": {
                  "host": {"type": "string", "description": "Hostname or host:port."},
                  "sandbox": {"type": "string",
                              "description": "Optional sandbox name to scope the check."}},
              "required": ["host"], "additionalProperties": False}),
        tool("sbx_run_test",
             "Create a disposable sandbox, run one command inside it, and return "
             "a receipt naming the sbx build, the command, the exit code and "
             "the timings. Requires SBX_MCP_ALLOW and an allowed command.",
             {"type": "object",
              "properties": {
                  "command": {"type": "array", "items": {"type": "string"},
                              "description": "The command and arguments, e.g. [\"npm\",\"test\"]."},
                  "workspace": {"type": "string",
                                "description": "Project directory to clone into the sandbox."},
                  "mode": {"type": "string", "enum": ["clone", "direct", "mountless"],
                           "description": "How the workspace reaches the sandbox."},
                  "timeout": {"type": "integer",
                              "description": "Seconds before the run is abandoned."}},
              "required": ["command"], "additionalProperties": False},
             read_only=False, destructive=True),
        tool("sbx_collect_file",
             "Copy one file out of a sandbox onto the host. Requires "
             "SBX_MCP_ALLOW and a named sandbox.",
             {"type": "object",
              "properties": {
                  "sandbox": {"type": "string", "description": "The sandbox name."},
                  "remote_path": {"type": "string", "description": "Path inside the sandbox."},
                  "destination": {"type": "string", "description": "Host path to write."}},
              "required": ["sandbox", "remote_path", "destination"],
              "additionalProperties": False},
             read_only=False, destructive=False),
    ]


# --------------------------------------------------------------------------
# Tool implementations
# --------------------------------------------------------------------------

def _sandbox():
    return runner.Sbx()


def tool_sbx_status(_arguments):
    sbx = _sandbox()
    version = sbx.version()
    state = sbx.daemon_state()
    sandboxes, error = runner.sandbox_inventory(sbx, state)
    return {
        "sbx_version": f"{version[0]}.{version[1]}.{version[2]}" if version else None,
        "daemon_state": state,
        "sandbox_count": len(sandboxes) if sandboxes is not None else None,
        "sandboxes_error": error,
        "capabilities": sbx.caps(),
    }


def tool_sbx_list_sandboxes(_arguments):
    sbx = _sandbox()
    return {"sandboxes": [
        {"name": row.get("name"), "agent": row.get("agent"),
         "status": row.get("status"), "workspaces": row.get("workspaces") or []}
        for row in sbx.sandboxes()]}


def tool_sbx_inspect_sandbox(arguments):
    name = arguments.get("name")
    if not isinstance(name, str) or not name.strip():
        raise McpError("invalid_arguments", "name is required")
    sbx = _sandbox()
    try:
        return {"sandbox": sbx.json(["inspect", name])}
    except runner.RunnerError as exc:
        raise McpError(exc.code, exc.detail) from exc


def tool_sbx_policy_check(arguments):
    host = arguments.get("host")
    if not isinstance(host, str) or not host.strip():
        raise McpError("invalid_arguments", "host is required")
    sbx = _sandbox()
    args = ["policy", "check", "network"]
    sandbox = arguments.get("sandbox")
    if isinstance(sandbox, str) and sandbox.strip():
        args += ["--sandbox", sandbox]
    args.append(host)
    rc, out, err = sbx.run(args, timeout=30)
    text = (out or err).decode("utf-8", "replace").strip()
    return {"host": host, "sandbox": sandbox, "allowed": rc == 0,
            "exit_code": rc, "detail": text}


def tool_sbx_run_test(arguments):
    command = arguments.get("command")
    if not isinstance(command, list) or not command or not all(
            isinstance(part, str) for part in command):
        raise McpError("invalid_arguments", "command must be a non-empty list of strings")
    if not command_permitted(command):
        raise McpError(
            "command_not_allowed",
            "this server may not run that command",
            {"requested": command, "allowed_prefixes": allowed_commands()})
    mode = arguments.get("mode", "clone")
    if mode not in ("clone", "direct", "mountless"):
        raise McpError("invalid_arguments", "mode must be clone, direct or mountless")
    workspace = arguments.get("workspace")
    if mode in ("clone", "direct") and not workspace:
        raise McpError("invalid_arguments", f"{mode} mode needs a workspace")
    timeout = arguments.get("timeout")
    timeout = int(timeout) if isinstance(timeout, int) and timeout > 0 else runner.DEFAULT_TIMEOUT

    sbx = _sandbox()
    name = "mcp-test-%05d" % (int(time.time() * 1000) % 100000)
    run = runner.SandboxRun(sbx, name, agent="shell", mode=mode,
                             workspace=workspace, keep=False, run_timeout=timeout)
    receipt = None
    try:
        run.create()
        result = run.exec(command)
        receipt = runner.build_receipt(run, result, command, workspace=workspace)
    except runner.RunnerError as exc:
        raise McpError(exc.code, exc.detail) from exc
    finally:
        if run.created:
            run.remove()
    # The receipt carries tails of the output; the agent gets the whole
    # result but not the sbx internals it does not need.
    return {
        "outcome": receipt["result"]["outcome"],
        "exit_code": receipt["result"]["exit_code"],
        "seconds": receipt["result"]["seconds"],
        "stdout_tail": receipt["result"]["stdout_tail"],
        "stderr_tail": receipt["result"]["stderr_tail"],
        "sandbox": receipt["sandbox"]["name"],
        "sbx_version": receipt["sbx"].get("client", {}).get("version"),
    }


def tool_sbx_collect_file(arguments):
    sandbox = arguments.get("sandbox")
    remote = arguments.get("remote_path")
    destination = arguments.get("destination")
    for label, value in (("sandbox", sandbox), ("remote_path", remote),
                         ("destination", destination)):
        if not isinstance(value, str) or not value.strip():
            raise McpError("invalid_arguments", f"{label} is required")
    sbx = _sandbox()
    rc, out, err = sbx.run(["cp", f"{sandbox}:{remote}", destination], timeout=120)
    if rc != 0:
        raise McpError("collect_failed",
                       (err or out).decode("utf-8", "replace").strip()[:400])
    return {"collected": True, "sandbox": sandbox, "remote_path": remote,
            "destination": destination}


HANDLERS = {
    "sbx_status": tool_sbx_status,
    "sbx_list_sandboxes": tool_sbx_list_sandboxes,
    "sbx_inspect_sandbox": tool_sbx_inspect_sandbox,
    "sbx_policy_check": tool_sbx_policy_check,
    "sbx_run_test": tool_sbx_run_test,
    "sbx_collect_file": tool_sbx_collect_file,
}


# --------------------------------------------------------------------------
# JSON-RPC plumbing
# --------------------------------------------------------------------------

def _result(request_id, payload):
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id, code, message, data=None):
    error = {"code": code, "message": message}
    if data:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def handle_request(request, state):
    """Handle one JSON-RPC request object and return the response object."""
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        return _result(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "Docker Sandboxes connector. Read tools are always available. "
                "Write tools exist only when this server was started with "
                "SBX_MCP_ALLOW naming them, and sbx_run_test additionally "
                "requires the command to match SBX_MCP_ALLOWED_COMMANDS. A "
                "refusal states the reason; do not retry it as another "
                "command."),
        })

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return _result(request_id, {})

    if method == "tools/list":
        permitted = set(allowed_tools())
        return _result(request_id, {
            "tools": [t for t in tool_definitions() if t["name"] in permitted]})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in HANDLERS:
            return _error(request_id, -32602, f"unknown tool: {name}")
        if name not in set(allowed_tools()):
            return _error(request_id, -32601, f"tool not enabled: {name}",
                          {"required_env": "SBX_MCP_ALLOW"})
        try:
            payload = HANDLERS[name](arguments)
        except McpError as exc:
            # A tool-level failure is a successful call with an error
            # content block, not a protocol error: the model should read it.
            return _result(request_id, {
                "content": [{"type": "text",
                             "text": json.dumps({"error": exc.code,
                                                 "message": exc.message,
                                                 "data": exc.data},
                                                sort_keys=True)}],
                "isError": True,
            })
        except Exception as exc:  # noqa: BLE001 - a tool must not kill the server
            return _result(request_id, {
                "content": [{"type": "text",
                             "text": json.dumps({"error": "tool_failed",
                                                 "message": str(exc)[:400]})}],
                "isError": True,
            })
        return _result(request_id, {
            "content": [{"type": "text", "text": json.dumps(payload, indent=2,
                                                            sort_keys=True)}],
            "structuredContent": payload,
            "isError": False,
        })

    if method == "resources/list":
        return _result(request_id, {"resources": []})

    if method == "prompts/list":
        return _result(request_id, {"prompts": []})

    return _error(request_id, -32601, f"method not found: {method}")


def serve(stdin=None, stdout=None):
    """Read newline-delimited JSON-RPC from stdin and answer on stdout."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    state = {}
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            stdout.write(json.dumps(_error(None, -32700, "parse error")) + "\n")
            stdout.flush()
            continue
        if not isinstance(request, dict):
            stdout.write(json.dumps(_error(None, -32600, "invalid request")) + "\n")
            stdout.flush()
            continue
        response = handle_request(request, state)
        if response is None:
            continue
        stdout.write(json.dumps(response) + "\n")
        stdout.flush()


def main():
    serve()


if __name__ == "__main__":
    main()
