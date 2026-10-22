# Connectors

Two connectors and one kit, for using Docker Sandboxes from the host and from
an agent that runs inside one.

| What | Where | What it does |
| --- | --- | --- |
| The runner | [`sbx-runner/`](sbx-runner/) | Creates a disposable sandbox, runs one command in it, writes a receipt, removes the sandbox. |
| The MCP server | [`sbx-mcp/`](sbx-mcp/) | Exposes the sandbox state to an agent over MCP, with reads always on and writes behind an environment gate. |
| The kit | [`kits/dashboard-tests/`](kits/dashboard-tests/) | Installs the Python toolchain into a sandbox and writes a runner script that leaves a tree digest and JUnit XML behind. |

## Why these exist

The `sbx` CLI is a host tool. Two things follow from that, and both shapes
here exist because of one of them.

**A script that shells out to `sbx` breaks when `sbx` changes.** The CLI
moved its flags and reshaped its JSON documents in v0.48.0, and it will move
again. A script that hard-codes `--detached` or that parses
`{"sandboxes": [...]}` fails on the new build, and the commonest failure is
an empty list rather than an error. The runner reads the installed version
once, picks the spelling that version understands, and normalises the shapes
it gets back, so one recipe runs on v0.45 through v0.49.x.

**An agent inside a sandbox cannot reach the host.** It has no host
filesystem and no route to your host's binaries. The answer is not a mount;
it is a named tool, reached through the SBX MCP gateway. The MCP server here
is one such tool.

## The rule both connectors follow

Reads and writes are separated by construction, not by convention.

The read path can only read. The write path is absent from `tools/list`
unless it was granted, and the grant is an environment variable that the
next reader can see. A refusal states the reason and names what would have
been allowed.

That is deliberate. A tool that is not there cannot be reviewed. A tool that
answers "no, and here is the allowlist" can be read, argued with and audited.

## Using them

```bash
# What is installed, and what does it support?
python3 connectors/sbx-runner/sbx_runner.py status

# Run a command in a disposable sandbox and keep the receipt.
python3 connectors/sbx-runner/sbx_runner.py run npm test \
    --workspace ~/Projects/some-project --receipt .local/receipts/run.json

# Register the MCP server so a sandboxed agent can reach it.
sbx mcp add sbx --command python3 --args "$PWD/connectors/sbx-mcp/sbx_mcp.py"

# Give one sandbox the read-only tools.
sbx run claude . --static-mcp sbx
```

The write tools are off until they are named:

```bash
SBX_MCP_ALLOW=sbx_run_test \
SBX_MCP_ALLOWED_COMMANDS="npm test" \
sbx mcp add sbx --command python3 --args "$PWD/connectors/sbx-mcp/sbx_mcp.py"
```

## Tests

Neither connector needs a sandbox, a login or a hypervisor to be tested:

```bash
python3 connectors/sbx-runner/test_sbx_runner.py
python3 connectors/sbx-mcp/test_sbx_mcp.py
```

The runner's test suite fakes `sbx` at the subprocess boundary, so it
exercises argument construction, the version-compat shims, the shape
normalisation and the receipt assembly. The connector's suite drives the
MCP surface in-process, plus one case that runs the real stdio transport
through a pipe so the framing is covered too.

## What these do not do

- They do not bypass the network policy. A sandbox that cannot reach a host
  cannot be made to reach it by a connector.
- They do not make a test result trustworthy on its own. The receipt records
  what ran; it does not judge whether the thing under test was correct.
- They do not manage sandboxes you did not create. The runner removes the
  sandbox it created, and nothing else.
