# sbx-runner

Run a command inside a disposable Docker Sandbox and keep a receipt that says
what happened.

```
python3 sbx_runner.py status
python3 sbx_runner.py run npm test --workspace . --receipt receipt.json
python3 sbx_runner.py selftest
```

## Why it exists

`sbx exec` prints to a terminal and forgets. Six months later nobody can
tell which build of `sbx` ran the command, what the tree looked like, or how
long it took. The runner writes that down.

And it survives the CLI moving. The Docker Sandboxes CLI changed its flag
spellings and the shape of its JSON documents in v0.48.0. A wrapper that
hard-codes one spelling breaks on the other, and the usual failure is an
empty sandbox list rather than an error. The runner reads the installed
version once, picks the spelling that version understands, and normalises
both list shapes:

| | Before v0.48.0 | From v0.48.0 |
| --- | --- | --- |
| `sbx run` detached | `--detached` | `--detach` |
| `sbx create` quiet | `--quiet` | `--no-progress` |
| `sbx diagnose` format | `--output` | `--format` |
| `sbx ls --json` | `{"sandboxes": [...]}` | a bare array |

## The receipt

```json
{
  "schema": "sbx-sandbox-test-receipt-v1",
  "harness": {"name": "sbx-runner", "version": "1.0.0", "sha256": "..."},
  "sbx": {"client": {"version": "v0.49.2"}, "server": {"state": "running"}},
  "sbx_capabilities": {"list_is_bare_array": true, "run_rm": true, "...": true},
  "sandbox": {"name": "sbxtest-1753...", "agent": "shell", "mode": "clone", "...": "..."},
  "command": ["npm", "test"],
  "source": {"workspace": "/Users/.../project", "workspace_tree_sha256": "...", "git_head": "..."},
  "result": {"exit_code": 0, "outcome": "pass", "seconds": 41.2,
             "stdout_tail": "...", "stderr_tail": "..."},
  "notes": [{"step": "create", "argv": ["..."], "exit_code": 0, "seconds": 8.1}]
}
```

The tree digest is an identity of the input, not a content digest of every
file: it names the files, their sizes and their modes, so a later reader can
tell whether this was the same tree. The harness digest says which version of
this code produced the receipt.

## Modes

| Mode | What the workspace does inside the sandbox |
| --- | --- |
| `clone` (default) | The repository is cloned into the sandbox; the host working tree is read-only at `/run/sandbox/source`. |
| `direct` | The workspace is bind-mounted read-write. The agent's edits land on your working tree. |
| `mountless` | No workspace at all. The sandbox shares nothing with the host. |

Both `clone` and `direct` require `--workspace` and pass its resolved absolute
path to `sbx create`. The selected source therefore matches the workspace in
the receipt even when the runner or MCP server starts in another directory.
Paths containing spaces are passed as a single argument.

`clone` is the default because it is the one that cannot surprise you. Its
limit is worth knowing: a clone-mode sandbox runs against the *committed*
state, so a test that depends on uncommitted changes sees the committed
code. The tree digest makes that difference visible instead of silent.

## What it does not do

- It does not judge the result. A zero exit status is a zero exit status.
- It does not bypass the network policy. A sandbox that cannot reach a host
  cannot be made to reach it by a runner.
- It does not touch sandboxes it did not create. The only `sbx rm` it ever
  issues names the sandbox it made.
- It does not read your environment. The receipt records the command and the
  tree, not the secrets in the environment that happened to be exported.

## Tests

```bash
python3 test_sbx_runner.py
```

The suite fakes `sbx` at the subprocess boundary, so no sandbox, daemon or
login is needed. It covers the version parsing, the capability boundaries at
each release, the list-shape normalisation for both layouts, the argument
construction for each mode, the output truncation and the receipt's shape.
It also checks clone selection through the command-line entry point from a
different directory, including agreement between the create arguments and
the receipt. The MCP connector's suite covers the same selection boundary.
