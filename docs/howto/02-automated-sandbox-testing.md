# Automated sandbox testing

## What this is for

This guide is for running a test suite inside a disposable Docker Sandbox with
no human present: a CI job, a pre-commit hook, a nightly check. The sandbox
comes up, the suite runs, the result comes back, and the sandbox goes away.
Nothing in it is interactive, nothing waits for a terminal, and nothing
depends on a person's typing `y`.

The shape is create → exec → remove, and the evidence is a receipt: a JSON
document that names the exact `sbx` build, the exact command, the exit status
and the timings, so a later reader can tell what actually ran without
trusting the runner.

The worked example at the end uses the connector in
`connectors/sbx-runner/`. Read that directory before you copy the recipe.

## Before you start

- The `sbx` CLI on your `PATH` and a Docker login: `sbx login`.
- A Docker personal access token with at least **Read** scope, exported as
  `DOCKER_PAT` in the CI environment. Ledger §8.
- A workspace directory that already exists. `sbx create` and `sbx run` **fail**
  when the workspace directory does not exist, and print a `mkdir -p` hint
  (ledger §4).
- A test command that exits non-zero on failure. `sbx exec` propagates the
  command's exit code, so this is the whole pass/fail mechanism.
- Provider credentials in the environment if the suite needs them
  (`sbx secret import --all`, below).

```bash
# Confirm the two things an unattended run depends on.
sbx version --json
test -d "$WORKSPACE" && echo "workspace ok"
```

## 1. Authenticate without a browser

Interactive `sbx login` opens a browser. In CI there is no browser, so feed it
the token on stdin:

```bash
echo "$DOCKER_PAT" | sbx login --username "$DOCKER_USER" --password-stdin
```

The token needs at least **Read** scope (ledger §8). Keep the value in the CI
secret store, not in the command line: `--password-stdin` exists precisely so
it does not land in shell history or in the process table.

Check the login without side effects:

```bash
sbx version --json    # server.state should read "running"
```

## 2. Import provider credentials

`sbx secret import --all` imports every provider credential found in the CI
environment into the global secret store (ledger §8). It is the one call that
gets an agent's model keys into a sandbox without a browser.

```bash
# See what would be imported first.
sbx secret import --dry-run

# Do it.
sbx secret import --all
```

`--dry-run` and `--force` are the flags on this command; `--all` suppresses
the per-entry prompts, which is what makes it usable unattended.

API keys are injected into HTTP headers by the host proxy and never stored in
the VM (ledger §10). A sandbox cannot read a key out of its own filesystem.

## 3. The create → exec → remove shape

This is the shape the ledger documents as the unattended form (ledger §8):

```bash
sbx create --name ci-task --clone claude .
sbx exec ci-task -- pytest -q
sbx rm --force ci-task
```

The ledger writes the middle line as `sbx exec ci-task -- …`, with the command
as the argument list after the `--` separator. `pytest -q` is a concrete
example; yours will be whatever your suite is.

Three things about it are worth stating explicitly.

**`sbx exec` starts a stopped sandbox first.** You do not need to `sbx start`
it. If the sandbox is stopped, `sbx exec` brings it up, runs the command and
leaves it running. The start message goes to **stderr**, not stdout, so it
does not contaminate a captured result.

**`sbx exec` has no detached mode.** The flag exists and is inert:

```
  -d, --detach   Detached mode (not supported)
```

Do not build a pipeline that depends on detaching from an exec.

**`sbx rm --force` skips the confirmation.** See the TTY rule below.

## 4. The "confirmation needs a TTY" rule

From ledger §4:

- A confirmation prompt **needs a terminal**.
- `echo y | sbx secret set …` no longer confirms. Piping `y` is not a
  confirmation mechanism.
- A command that needs confirmation **exits 1** when there is no TTY.
- Declining returns non-zero.
- Ctrl-D cancels rather than accepting the default.

So the rule for automation is: **pass the flag that skips the prompt. Do not
pipe input.**

| Command | v0.45.0-rc2 | v0.48.0+ |
| --- | --- | --- |
| `sbx rm` | `-f`, `--force` | `-f`, `--force` |
| `sbx prune` | `-f`, `--force` | `-f`, `--force` |
| `sbx logout` | `--yes`, `-y` | `--force`, `-f` |
| `sbx env plan\|create\|run` | `--auto-approve`, `-y` | `--force`, `-f` |

`sbx kit builder history rm` is the exception: it keeps `--yes`/`-y`, because
its `--force` is forwarded to Buildx (ledger §3).

The consequence for a CI script: **exit code 1 with no output on stdout is a
missing TTY, not a test failure.** Capture stderr and look for it.

## 5. Exit codes and the stream split

Results go to **stdout**. Errors, warnings, corrective hints, daemon-start
progress, image pulls and kit-build progress go to **stderr** (ledger §4).

A script that captures only stdout will miss the reason a command failed.
That is the single most common way an unattended `sbx` pipeline goes wrong.

`sbx exec` propagates the inner command's exit status. Verified on this Mac
against a stopped sandbox:

```console
$ sbx exec ci-task -- sh -c 'echo to-stdout; echo to-stderr >&2; exit 7'
exit=7
```

With `to-stdout` on stdout and both `Sandbox ci-task started successfully` and
`to-stderr` on stderr. Verified on this Mac against a stopped sandbox.

So the rules are:

| Question | Answer |
| --- | --- |
| How do I detect pass? | `sbx exec` exits 0. |
| How do I detect failure? | `sbx exec` exits non-zero with the command's own code. |
| Where is the test output? | stdout. |
| Where is the reason for a failure? | stderr. |
| Where does the "starting sandbox" noise go? | stderr. |
| Where does the image-pull progress go? | stderr. |

Capture both, and keep them apart:

```bash
sbx exec "$NAME" -- pytest -q > results.txt 2> results.err
status=$?
printf 'exit=%s\n' "$status"
printf '%s\n' "--- stdout ---"; cat results.txt
printf '%s\n' "--- stderr ---"; cat results.err
exit "$status"
```

## 6. Timeouts

`sbx exec` has **no timeout flag** (ledger §8 and the local `--help`). A
command that hangs will hang the pipeline.

Two options:

**Use the runner.** `connectors/sbx-runner/sbx_runner.py` enforces timeouts at
the subprocess boundary: 300 s for create, 900 s for exec, 120 s for remove,
all overridable. A timeout raises `RunnerError("sbx_timeout", …)` and the
process exits 2.

**Or wrap it in the shell.** On macOS, `timeout` is not in the base system;
install GNU coreutils and use `gtimeout`:

```bash
gtimeout --signal=KILL 900 sbx exec "$NAME" -- pytest -q
```

The runner's approach is better: it can kill the child, record the timeout in
the receipt, and still remove the sandbox.

## 7. Capturing JUnit XML

JUnit XML is a file, so it has to be copied out of the sandbox. `sbx cp SRC DST`
takes `SANDBOX:PATH` on one side (ledger §15). Copying between two sandboxes
is not supported.

```bash
NAME=ci-task

sbx create --name "$NAME" --clone claude .
sbx exec "$NAME" -- pytest -q --junitxml=/tmp/junit.xml
sbx cp "$NAME:/tmp/junit.xml" ./junit.xml
sbx rm --force "$NAME"
```

Notes on `sbx cp` (ledger §15):

- A directory is placed at the destination; if the destination exists as a
  directory, the source goes inside it.
- From v0.48.0, local paths that escape a sandbox workspace through symbolic
  links are blocked.
- From v0.49.1, a missing destination directory is reported without a pile of
  tar errors. On v0.45.0-rc2 the old behaviour is still there: **create the
  destination directory yourself** before copying.

```bash
mkdir -p ./artifacts
sbx cp "$NAME:/tmp/junit.xml" ./artifacts/junit.xml
```

The `dashboard-tests` kit in `connectors/kits/dashboard-tests/` does this
inside the sandbox: it writes a tree digest and `junit.xml` next to the code
it tested, under `$WORKSPACE/.sbx-test-results/`. See `05-kits-and-supply-chain.md`.

## 8. Clone mode is the safe default

The runner defaults to `--mode clone`. That is the right default for
unattended testing, and the reason is not "clone is more isolated" — clone mode
protects against **modification**, not inspection.

| | Direct | Clone (`--clone`) | Host worktree |
| --- | --- | --- | --- |
| Branch management | You, on the host | The agent, in the clone | You, on the host |
| Changes visible on the host | Immediately | After a fetch or a push | Immediately |
| The agent can use Git | Yes | Yes | **No** |
| Parallelism | No | Several agents in one sandbox | One sandbox per task |
| Fixed at creation | No | **Yes** | — |

(Ledger §11.)

What direct mode costs you:

- **Your working tree is the sandbox's working tree.** A test that writes a
  file, runs a formatter, or leaves a build artefact behind changes your host
  checkout. A test that runs `git checkout .` or `git clean -fdx` on the host
  copy is catastrophic.
- **You cannot run two of them at once.** Direct mode has no parallelism: two
  direct-mode sandboxes on the same checkout will fight over the index and the
  working tree.
- **Filesystem access policies do not protect you.** Ledger §11: direct-mode
  hard links reach outside the workspace, and filesystem access policies do
  not block that.
- **Nothing is isolated from a bad test.** The whole point of a disposable
  sandbox is that a bad test destroys the sandbox, not your work.

What clone mode gives you:

- The sandbox works on a private in-container clone. The source is mounted
  read-only at `/run/sandbox/source`, so a write attempt fails at the
  filesystem level.
- The agent can branch, commit and push from inside the sandbox.
- Several agents can work in one sandbox.
- The mode is **fixed at creation**. You cannot convert a direct sandbox to a
  clone sandbox, or the reverse.

Clone mode does not hide anything. The read-only source mount covers the whole
working directory **including untracked and ignored files**, so a `.env` in
your checkout is readable from the sandbox. Clone mode protects against
modification, not inspection.

If you need the agent's changes back on the host, the clone-mode remote is a
`sandbox-<name>` remote served by a git-daemon inside the sandbox, bound to
localhost on the host. `sbx stop` stops it; a restart assigns a new ephemeral
port and the CLI rewrites the remote URL; `sbx rm` removes the remote entry.

## 9. Prune, and looking before you leap

`sbx prune` removes all **stopped** sandboxes. A running sandbox is never a
candidate, which is what makes it safe to run habitually. From v0.47.0
`sbx run --rm` does the same job for a single run.

Preview first:

```bash
sbx prune --dry-run --json
```

On v0.45.0-rc2 the machine-readable flag is `--json`:

```json
{
  "would_remove": [
    {
      "name": "m15219-v44-benchmark-20261016",
      "agent": "shell",
      "stopped_at": "2026-10-16T10:42:19Z",
      "workspaces": ["/tmp/m15219-v44-sandbox-benchmark"],
      "workspace_missing": true
    }
  ]
}
```

The ledger's automation page documents the newer spelling,
`sbx prune --dry-run --format json` (ledger §8). On v0.45.0-rc2, `--format` is
an **unknown flag** and the command exits non-zero with
`error: unknown flag: --format`. Use `--json` on the old binary; it is an
alias for `--format json` from v0.48.0.

Pruning needs confirmation. Use `--force` in a script:

```bash
sbx prune --dry-run --json | jq -r '.would_remove[].name'
sbx prune --force
```

Secrets scoped to each successfully pruned sandbox are deleted with it. Only
stopped sandboxes are candidates; a sandbox whose stop time the daemon cannot
report is left alone.

## 10. Worked example: the sbx-runner connector

### What it is

`connectors/sbx-runner/sbx_runner.py` is a dependency-free Python wrapper
around the `sbx` binary. It reads the installed `sbx` version once, picks the
spelling that version understands, normalises the shapes it gets back, and
writes a receipt.

It has three subcommands:

| Subcommand | What it does |
| --- | --- |
| `status` | Report the installed `sbx`, the daemon state, the sandboxes, and a set of version-derived notes. |
| `run` | Create a sandbox, run one command in it, write a receipt, remove the sandbox. |
| `selftest` | Exercise the version-compat logic without touching a sandbox. |

The global `--sbx PATH` option points it at a specific binary.

### The `run` flags, exactly as they are

| Flag | Default | Meaning |
| --- | --- | --- |
| `command` (positional, repeatable) | — | The command and its arguments. |
| `--workspace PATH` | — | The project directory. **Required** for `clone` and `direct`. |
| `--name NAME` | `sbxtest-<epoch>` | Sandbox name. Must match `[a-z0-9][a-z0-9.-]{1,63}`. |
| `--agent AGENT` | `shell` | Agent to provision. |
| `--mode MODE` | `clone` | One of `clone`, `direct`, `mountless`. |
| `--workdir DIR` | — | Working directory inside the sandbox. |
| `--kit REF` | — | Kit reference. Repeatable. |
| `--cpus N` | `2` | CPUs. |
| `--memory M` | `4g` | Memory limit. |
| `--skills MODE` | `off` | One of `off`, `readonly`, `readwrite`. |
| `--timeout SECONDS` | `900` | The exec timeout. |
| `--receipt PATH` | — | Write the receipt JSON to this path. |
| `--keep` | off | Do not remove the sandbox. |
| `--json` | off | Print the receipt to stdout. |

### How it builds the create command

The agent is positional: `sbx create AGENT [PATH]`. The runner assembles:

- **`clone`** → `create <agent> --name <name> --clone <resolved-workspace-path>`
  plus sizing, skills and kits. The explicit path selects the repository to
  clone, independent of the runner's current working directory. It is also
  recorded in the receipt and used to compute the source digest and Git HEAD.
- **`direct`** → `create <agent> --name <name> <resolved-workspace-path>`
  plus the same extras. The path is resolved to an absolute path first.
- **`mountless`** → `create <agent> --name <name>` plus the same extras. No
  path at all; the agent works in the container's own filesystem.

`--skills off` is the default. That is deliberate: a shared skills store
mounted `readwrite` puts participating sandboxes in one trust boundary
(ledger §10). A test harness has no business widening that.

### How it builds the exec command

```
exec [--workdir DIR] [--user USER] [--env K=V]… <name> -- <command…>
```

The `--` separator is always present, and the command is passed as a list, so
no shell quoting is involved.

### The receipt

Schema `sbx-sandbox-test-receipt-v1`. It carries:

- `harness` — name, version, and the SHA-256 of the runner file itself, so
  you can tell which runner produced the document.
- `sbx` — the raw `sbx version --json` document, and `sbx_capabilities`, the
  version-derived capability record.
- `sandbox` — name, agent, mode, cpus, memory, skills, kits, whether it was
  kept.
- `command` — the exact argv.
- `source` — the workspace path, a `workspace_tree_sha256` (a digest of the
  tree's names, sizes and modes, path-independent, `.git` excluded), and
  `git_head`.
- `result` — argv, exit code, elapsed seconds, stdout and stderr byte
  counts, and the **tails** of stdout and stderr, plus `outcome` (`pass` when
  the exit code is 0, `fail` otherwise).
- `notes` — the create step, and the removal step.

Output is bounded. The tail kept per stream is 16 KiB, with a
`...[truncated]...` marker; the full receipt is refused above 1 MiB. The
byte counts survive the truncation, so you can still see that a test printed
40 MiB.

### Running it

```bash
# 1. What is installed?
python3 connectors/sbx-runner/sbx_runner.py status
python3 connectors/sbx-runner/sbx_runner.py status --json

# 2. The version logic, without a sandbox.
python3 connectors/sbx-runner/sbx_runner.py selftest

# 3. The unit tests.
python3 connectors/sbx-runner/test_sbx_runner.py
```

A single test run:

```bash
python3 connectors/sbx-runner/sbx_runner.py run \
  --workspace "$PWD" \
  --name ci-task \
  --mode clone \
  --timeout 900 \
  --receipt ./receipts/ci-task.json \
  -- \
  python3 -m pytest -q --junitxml=/tmp/junit.xml
```

Then copy the JUnit XML out (the runner does not do this for you):

```bash
sbx cp ci-task:/tmp/junit.xml ./junit.xml
```

Wait — the runner removes the sandbox at the end of `run`, so the copy has to
happen inside the run, or you need `--keep`:

```bash
# Keep the sandbox so you can pull artefacts out of it.
python3 connectors/sbx-runner/sbx_runner.py run \
  --workspace "$PWD" --name ci-task --mode clone --keep \
  -- sh -c 'python3 -m pytest -q --junitxml=/tmp/junit.xml'
sbx cp ci-task:/tmp/junit.xml ./junit.xml
sbx rm --force ci-task
```

The runner's own exit codes:

| Code | Meaning |
| --- | --- |
| 0 | The sandbox command exited 0. |
| 1 | The sandbox command exited non-zero. The receipt says which. |
| 2 | `RunnerError`: the sandbox could not be created, the command timed out, the output was not parseable, the workspace was missing, the name was invalid, and so on. The error code and detail are printed to **stderr** as JSON. |

### The `status` notes

`status` prints notes derived from the version. On v0.45.0-rc2 it tells you
three things:

- The installed `sbx` is a release candidate.
- On v0.49.2 or later: the built-in template images were rebuilt for the
  eMudhra CA compromise, and existing sandboxes keep the old CA store —
  recreate with `--pull always`, or upgrade `ca-certificates` inside them.
- On anything below v0.48.0: `sbx ls --json` still wraps the list in
  `{"sandboxes": [...]}` and the old flag spellings are in use.

## 11. The shell recipe

Everything the runner does, in plain shell, for a pipeline where you would
rather not depend on Python:

```bash
#!/bin/sh
# Unattended test run: create -> exec -> remove.
set -eu

WORKSPACE="$PWD"
NAME="ci-$(date -u +%Y%m%dT%H%M%SZ)"
RESULTS="${RUNNER_TEMP:-/tmp}/sbx-ci"
mkdir -p "$RESULTS"

cleanup() {
  sbx rm --force "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

# 1. Non-interactive login.
echo "$DOCKER_PAT" | sbx login --username "$DOCKER_USER" --password-stdin

# 2. Provider credentials from the CI environment.
sbx secret import --all

# 3. Create. The workspace must already exist.
sbx create --name "$NAME" --clone claude "$WORKSPACE"

# 4. Exec. stdout is the result; stderr is everything else.
set +e
sbx exec "$NAME" -- sh -c 'python3 -m pytest -q --junitxml=/tmp/junit.xml' \
  > "$RESULTS/stdout.txt" 2> "$RESULTS/stderr.txt"
status=$?
set -e

# 5. Pull the artefact out.
sbx cp "$NAME:/tmp/junit.xml" "$RESULTS/junit.xml" || true

# 6. Report, then leave with the suite's status.
printf 'sandbox   %s\n' "$NAME"
printf 'exit      %s\n' "$status"
printf 'results   %s\n' "$RESULTS"
if [ -s "$RESULTS/stderr.txt" ]; then
  printf '%s\n' '--- stderr ---'
  cat "$RESULTS/stderr.txt"
fi
exit "$status"
```

Two things to notice in that recipe. The `trap` removes the sandbox even when
the suite fails, so a red build does not leak sandboxes. And the exit status
comes from `sbx exec`, not from the `sbx cp` at the end — the copy is allowed
to fail without changing the verdict.

## What this does not cover

- **Parallel test execution.** The runner runs one command in one sandbox.
  Ledger §11 says clone mode supports "several agents in one sandbox", but
  nothing here schedules them.
- **Cloud sandboxes.** Everything above is local. Cloud sandboxes have no host
  workspace, expire on a TTL, and do not support `sbx exec` the same way.
- **Windows and Linux hosts.** The paths and the `gtimeout` caveat are
  macOS-shaped. The ledger's automation page is the platform-neutral source.
- **The `sbx run --rm` shortcut** introduced in v0.47.0. It is one command
  instead of three, but it gives you no receipt, so it is not what you want
  when the point of the run is the evidence.
- **Agent authentication inside the sandbox.** `sbx secret import --all` moves
  provider credentials into the secret store. It does not cover the separate
  question of an agent CLI that needs its own login; see
  `08-troubleshooting.md` for the `PROXY` column of `sbx policy log`.
- **What the tests themselves do.** This guide runs a command. It does not
  install a toolchain, resolve dependencies, or provision a database. That is
  what kits are for: `05-kits-and-supply-chain.md`.
