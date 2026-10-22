# sbx-mcp

An MCP stdio server that exposes Docker Sandboxes to an agent.

```
sbx mcp add sbx --command python3 --args "/path/to/connectors/sbx-mcp/sbx_mcp.py"
sbx run claude . --static-mcp sbx
```

## The problem it solves

A sandbox has no host filesystem and no route to your host's binaries. When
an agent inside one needs to reach something on your Mac, you do not mount
it: you give it a named tool. A host program that speaks MCP, reachable
through the SBX MCP gateway, whose tool list *is* the scope of what the
agent can do.

This server is one of those programs.

## Two doors, not one door with a lock

| Tools | When they exist | What they can do |
| --- | --- | --- |
| `sbx_status`, `sbx_list_sandboxes`, `sbx_inspect_sandbox`, `sbx_policy_check` | Always. | Only read. They cannot start, stop or remove anything. |
| `sbx_run_test` | Only when `SBX_MCP_ALLOW` names it, and only when the command matches `SBX_MCP_ALLOWED_COMMANDS`. | Creates a disposable sandbox, runs one command, returns a receipt, removes the sandbox. |
| `sbx_collect_file` | Only when `SBX_MCP_ALLOW` names it. | Copies one file out of a sandbox. |

A tool that is not granted does not appear in `tools/list` at all. A tool
that is granted but refuses does so with the reason:

```json
{
  "error": "command_not_allowed",
  "message": "this server may not run that command",
  "data": {"requested": ["rm", "-rf", "/"], "allowed_prefixes": ["npm test"]}
}
```

That refusal is the design, not a limitation of it. A tool that is absent
cannot be reviewed; a tool that answers "no, and here is the allowlist" can
be read, argued with and audited.

## The environment variables

| Variable | Meaning | Default |
| --- | --- | --- |
| `SBX_MCP_ALLOW` | Comma-separated write tools to expose. | none |
| `SBX_MCP_ALLOWED_COMMANDS` | Comma-separated command prefixes `sbx_run_test` may run. | none, so nothing runs |

The prefix matches the whole word, not a substring: `npm test` allows
`npm test -- --watch` and does not allow `npm run test`, and it does not
allow `rm`.

## The tools

| Tool | Reads or writes | What it returns |
| --- | --- | --- |
| `sbx_status` | read | The client version, the daemon state, the sandbox count, and the capability set for that version. |
| `sbx_list_sandboxes` | read | Every sandbox with its agent, status and workspaces. |
| `sbx_inspect_sandbox` | read | One sandbox's full inspection record: kits, mounts, network policy, secrets and ports. |
| `sbx_policy_check` | read | Whether a destination would be allowed, and whether an explicit rule exists. |
| `sbx_run_test` | write | A receipt: the outcome, the exit code, the timings, the output tails, the sandbox name and the sbx version. |
| `sbx_collect_file` | write | Confirmation that a file was copied out of a sandbox. |

The read tools use the same version-compat shims as the runner, so they
report a pre-v0.48 build's list shape correctly as well as a newer one's.

## The transport

Newline-delimited JSON-RPC 2.0 on stdio, which is what MCP's stdio
transport uses: one JSON object per line, in both directions. The server
speaks protocol version `2025-11-25`, exposes `tools/list`, `tools/call`,
`initialize`, `ping`, `resources/list` and `prompts/list`, and answers a
malformed line with a parse error without dying.

A tool failure is a successful call carrying an error content block, not a
protocol error: the model should read what went wrong rather than see the
transport break.

## Tests

```bash
python3 test_sbx_mcp.py
```

The suite exercises the MCP surface in-process and never invokes `sbx`. It
covers the tool grant rules, the command-prefix matching, the argument
validation, the error shapes, and the newline-delimited framing through a
pipe.
