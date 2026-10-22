# Versions and upgrading

## What this is for

The `sbx` binary on this Mac is **v0.45.0-rc2**. Three minor releases came out
after it, and the middle one — v0.48.0 — renamed flags and changed the shape
of several JSON documents. A script written against v0.45 does not fail loudly
when it meets v0.49; it returns an empty list, or a different exit code, or a
document that no longer has the key it used to read. This guide is the upgrade
path: what changed, how to find the breakages in your own scripts before they
find you, how to respond to the eMudhra CA compromise, and how to write one
script that works on both spellings.

Every version fact here comes from [`00-verified-facts.md`](00-verified-facts.md).
That file is the source of truth; this one is the applied version of it.

## Before you start

- The `sbx` CLI on your `PATH` (on this Mac: `/opt/homebrew/bin/sbx`).
- A Docker login: `sbx login`.
- Read access to the scripts you intend to check for v0.48.0 breakage.
- A copy of any sandbox whose filesystem you care about. Removing a sandbox
  deletes its filesystem (ledger §9).

```bash
# What is installed, and what is running behind it.
sbx version --json

# Where the daemon socket lives (ledger §2).
ls -l ~/.sbx/run/d/sandboxd.sock
```

## Step 1: Know which version you have

`sbx version --json` reports two halves. The client is the binary you invoked.
The server is the daemon behind the socket. They are the same on a clean
install, and they are **not** the same after a partial upgrade.

On this Mac the document reads:

```json
{"client": {"version": "v0.45.0-rc2", "revision": "4db7091451a820a472944415bbb0b3ac8bb04701", "build_tags": "cloud"},
 "server":  {"state": "running", "version": "v0.45.0-rc2", "revision": "4db7091451a820a472944415bbb0b3ac8bb04701", "api_version": "0.36.0"}}
```

Read it like this:

| Field | What it tells you |
| --- | --- |
| `client.version` | The flag spellings and JSON shapes you will get. |
| `client.revision` | The exact build. Quote it in a bug report. |
| `server.version` | Whether the daemon agrees with the client. |
| `server.state` | `running`, `stopped`, or absent. |
| `server.api_version` | The daemon API contract. A mismatch with the client is what `sbx diagnose` flags. |

`sbx version --json` reports `server.state` **without** starting the daemon.
`sbx ls` starts it. That asymmetry matters when you are deciding whether a
command "hung" or was merely cold.

If `client.version` and `server.version` differ, the daemon is stale. Restart
it before you debug anything else:

```bash
sbx daemon restart
sbx version --json
```

## Step 2: What each release changed, in practical terms

Full table in ledger §1. The short version, in the order you will meet it:

| Release | Practical effect on you |
| --- | --- |
| v0.45.0 | Cloud hostname allowlists stopped gaining implicit `0.0.0.0/0` and `::/0` rules. If you relied on "the allowlist covers everything", that is no longer true. |
| v0.46.0 | Raw TCP to a *denied* hostname is no longer permitted by an allow rule for that hostname's resolved IP. The UDP case with multiple tracked hostnames is still open (ledger §17). |
| v0.47.0 | `sbx run --rm` exists. `sbx kit sign` and `sbx kit push --sign` work on registries that refuse manifest deletion (GHCR, Docker Hub). Kit pulls enforce blob, decompressed-size and entry-count limits, so an oversized kit now fails to pull instead of failing to unpack. |
| v0.48.0 | **Breaking.** Flag renames and JSON shape changes. Also: `sbx setup ssh --auto-create`, v3 kit capability groups, `ssh-agent@1` and `git-identity@1`, `sbx kit add` with startup commands, `SBX_AGENT`, `sbx mcp unload`, `sbx run --model --provider`, `sbx run --variant`. |
| v0.49.0 | Skipped. |
| v0.49.1 | `sbx kit validate|inspect --offline`; `kit.allowedSources` and `kit.trustedSigners` structure checks before saving; `browser.enabled` and `daemon.log.*` settings; `sbx policy refresh`; `sbx cp` reports a missing destination directory; `sbx diagnose` reports running CLI and shim processes and warns when the global network policy is uninitialised; an explicit CIDR deny beats a matching domain allow; full templates gain a C/C++ toolchain. |
| v0.49.2 | The eMudhra CA compromise response. New Go toolchain, new bundled `llmman`, rebuilt built-in template images. **The CLI update alone does not update existing sandboxes.** |

Nightlies are published every day under the `nightly-*` tag
(`sbx@nightly` Homebrew cask). They are prereleases and carry provenance and
SBOM assets. The table above does not cover them, and neither does this guide.

## Step 3: Find the v0.48.0 breakages in your own scripts

### 3a. The flag renames

From ledger §3:

| Command | Old spelling | Replacement |
| --- | --- | --- |
| `sbx run`, `sbx env run` | `--detached` | `--detach` / `-d` |
| `sbx logout` | `--yes`, `-y` | `--force` / `-f` |
| `sbx env plan\|create\|run` | `--auto-approve`, `-y` | `--force` / `-f` |
| `sbx create` | `--quiet`, `-q` | `--no-progress` |
| `sbx template save` | `-d` (description) | `--description` |
| `sbx diagnose` | `--output`, `-o` | `--format table\|json\|yaml\|github-issue` |
| `sbx secret ls`, `sbx secret rm` | `--env` | `--env-name` |
| `sbx policy log` | `--quiet`, `-q` | removed, no replacement |
| `sbx mcp add` | `--skip_auth` | `--skip-auth` (old spelling still accepted, hidden, warns) |

Two of those are traps:

- `sbx policy log --quiet` was **removed with no replacement**. A script that
  passes it fails on v0.48.0.
- `sbx mcp add --skip_auth` still works on v0.48.0+, but it is hidden and
  warns on stderr. If you capture stderr, that warning is now part of your
  output. Change the spelling rather than filtering the warning.

Note also that `sbx kit builder history rm` keeps `--yes`/`-y`, because its
`--force` is forwarded to Buildx. A blanket "replace `-y` with `-f`" rewrite
is wrong.

### 3b. The JSON shape changes

From ledger §4. The full table lives there; the ones that bite hardest:

| Command | v0.45 shape | v0.48+ shape |
| --- | --- | --- |
| `sbx ls` | `{"sandboxes": [...]}` | bare array |
| `sbx template ls` | `{"images": [...]}` | bare array |
| `sbx mcp ls` (local) | `{"gateway": {...}, "servers": [...]}` | bare array; each row carries `gateway` |
| `sbx policy log` | `allowed_hosts` / `blocked_hosts` | `{"updated_at", "allowed": [...], "blocked": [...]}` |
| `sbx rm`, `sbx stop`, `sbx template rm`, `sbx volume rm` | one line per target | array of `{action, resource, name, id?, error?}` |

`.sandboxes[].name` becomes `.[].name`.

A script that does `sbx ls --json | jq '.sandboxes[].name'` returns **empty**
on v0.48+, not an error. That is the failure mode to look for.

### 3c. The grep

Run this over your scripts and shell history. It catches the old flag
spellings:

```bash
grep -rnE -- \
  -- '--detached|--auto-approve|--skip_auth|-o +json|--output +json' \
  scripts connectors chapters 2>/dev/null
```

Add the two that need word boundaries, because `--env` and `--quiet` also
belong to `git`:

```bash
grep -rnE -- \
  '(^|[[:space:]])sbx[[:space:]].*--env[[:space:]]|(^|[[:space:]])sbx[[:space:]].*--quiet|(^|[[:space:]])sbx[[:space:]].*-o[[:space:]]' \
  scripts connectors chapters 2>/dev/null
```

And the shape readers, which are the quiet failures:

```bash
grep -rnE -- \
  '\.sandboxes\[|\.images\[|"sandboxes"|"images"|allowed_hosts|blocked_hosts|\.servers\[' \
  scripts connectors chapters 2>/dev/null
```

Nothing in this repository's `scripts/` currently uses the renamed flags
directly, because the workshop scripts drive `sbx` through
`connectors/sbx-runner/sbx_runner.py`, which already handles both spellings.
Your own scripts will not be so lucky.

## Step 4: The eMudhra CA compromise

Eight eMudhra intermediate CA private keys were disclosed on 2026-10-20.
Docker rebuilt the sandbox template images for all built-in kits and released
v0.49.2. Ledger §9 has the details.

**Updating the CLI does not update existing sandboxes.** This is the sentence
that matters. A sandbox created before v0.49.2 carries the old CA store, and
it keeps it until you recreate it or upgrade the store inside it.

### Option A: recreate the sandbox with fresh images

Correct when the sandbox is disposable, or when you can rebuild its contents.

```bash
# Save anything that lives only in the sandbox first.
sbx cp my-sandbox:/home/agent/important-output/ ./recovered/

sbx stop my-sandbox
sbx rm my-sandbox
sbx create --name my-sandbox --pull always codex /absolute/path/to/workspace
```

`--pull always` forces a fresh image pull, so the sandbox is built from the
rebuilt template that carries the corrected CA bundle.

Removing a sandbox deletes its filesystem. Save files **and unpushed
clone-mode commits** first. In clone mode the agent's commits live in the
sandbox's clone, not on your host, until they are pushed (ledger §11).

### Option B: update the CA store in place

Correct when the sandbox holds state you cannot rebuild.

```bash
sbx exec my-sandbox -- sudo apt-get update
sbx exec my-sandbox -- sudo apt-get install --only-upgrade -y ca-certificates
```

This path does **not** cover:

- Custom templates. Rebuild those instead; a custom template is not one of the
  images Docker rebuilt.
- Tools that bundle their own CA stores. Those need their own update.

### Choosing

| Situation | Do this |
| --- | --- |
| Sandbox is disposable, or contents are reproducible | Option A: recreate with `--pull always`. |
| Sandbox holds a database, a long-running process, or unrecoverable files | Option B: upgrade `ca-certificates` in place. |
| Sandbox uses a custom template | Rebuild the custom template image. Option B does not reach it. |
| You need certainty, not a judgement call | Do both: upgrade in place to stop the bleeding, then recreate when convenient. |

## Step 5: A compatibility shim for scripts that must run on both spellings

The pattern: **ask the version once, decide the spelling once, and never
guess at parse time.**

### 5a. The flag shim in shell

```bash
#!/bin/sh
# Resolve the sbx flags this host understands, once.
sbx_json_flag() {
  version=$(sbx version --json 2>/dev/null | sed -n 's/.*"client".*"version": *"v\{0,1\}\([0-9]*\)\.\([0-9]*\)\.\([0-9]*\).*/\1 \2 \3/p' | head -1)
  set -- $version
  major=${1:-0}; minor=${2:-0}; patch=${3:-0}
  if [ "$major" -gt 0 ] || [ "$minor" -ge 48 ]; then
    printf '%s' "--format json"
  else
    printf '%s' "--json"
  fi
}
```

Note the parse: `v0.45.0-rc2` must be read as `0.45.0`, not as a prerelease
that sorts below `0.45.0`. The `sbx-runner` connector does the same thing
with a regular expression rather than `sed`, and treats an unparseable version
as "assume the old spelling" — the conservative direction.

Then:

```bash
sbx_json_flag > /dev/null
sbx diagnose "$(sbx_json_flag)" > /dev/null
```

### 5b. The shape shim

Never index into the document without checking its type first:

```bash
# Normalise `sbx ls` across the v0.45 and v0.48+ shapes.
sbx_list_names() {
  sbx ls --json 2>/dev/null | jq -r '
    if type == "array" then .[]
    elif (.sandboxes? | type) == "array" then .sandboxes[]
    else empty end
    | .name // empty
  '
}
```

The same trick in Python, which is what `connectors/sbx-runner/sbx_runner.py`
does in `normalise_sandbox_list()`:

```python
def normalise_sandbox_list(document, expect_bare_array=None):
    """Normalise `sbx ls --json` across the v0.45 and v0.48+ shapes."""
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
```

Two properties of that function matter:

1. It returns an **error**, not an empty list, when the shape is unusable.
   An empty list is indistinguishable from "no sandboxes", and a script that
   treats that as success will happily continue.
2. It accepts `items` and `data` as well as `sandboxes`. Those are not in
   the ledger; they are defensive aliases so a future rename is a
   non-event rather than a silent empty.

### 5c. The capability table

The cleanest form of the shim is to turn the version into a small capability
record, and have the rest of the script read that record instead of repeating
version comparisons:

```python
def capabilities(version):
    """Describe what the installed sbx can do."""
    return {
        "detach_flag": "--detach" if at_least(version, 0, 48) else "--detached",
        "no_progress_flag": "--no-progress" if at_least(version, 0, 48) else "--quiet",
        "diagnose_format_flag": "--format" if at_least(version, 0, 48) else "--output",
        "list_is_bare_array": at_least(version, 0, 48),
        "run_rm": at_least(version, 0, 47),
        "mcp_unload": at_least(version, 0, 48),
        "policy_refresh": at_least(version, 0, 49, 1),
        "kit_validate_offline": at_least(version, 0, 49, 1),
        "rebuilt_ca_images": at_least(version, 0, 49, 2),
    }
```

`capabilities()` in `connectors/sbx-runner/sbx_runner.py` is exactly this, and
it is the reference implementation. `connectors/sbx-mcp/sbx_mcp.py` imports it
from there so the two connectors cannot disagree about the CLI surface.

### 5d. Test the shim without a sandbox

```bash
python3 connectors/sbx-runner/test_sbx_runner.py
python3 connectors/sbx-runner/sbx_runner.py selftest
```

The test file fakes `sbx` at the subprocess boundary, so it covers the
argument construction, the version shims, the shape normalisation and the
receipt assembly without a daemon, a login or a hypervisor.

## Step 6: Upgrade procedure

Order matters. The CLI and the daemon move together; the sandbox images move
separately.

```bash
# 1. Record what you are running now. Keep the output; you will want it.
sbx version --json | tee upgrade-before.json

# 2. Make sure you can get back to a clean state.
sbx ls --json
sbx diagnose --json > diagnose-before.json

# 3. Update the CLI. (Install command not recorded in the ledger — see below.)
#    On this Mac the binary is a Homebrew cask from the docker/tap tap.

# 4. Restart the daemon so the client and server agree.
sbx daemon restart
sbx version --json

# 5. Re-run the diagnostics.
sbx diagnose --json > diagnose-after.json

# 6. Recreate sandboxes that need the rebuilt images (see Step 4).
sbx create --name my-sandbox --pull always codex /absolute/path/to/workspace
```

`sbx diagnose` checks for a missing CLI binary, daemon reachability, a CLI/daemon
version mismatch, missing storage directories, broken authentication,
`mkfs.ext4`, running CLI and shim processes, an uninitialised global network
policy, and a ZFS setting that can slow image builds. Ledger §16 has the
detail.

Note the `--format` rename: on v0.45.0-rc2 the flag is `--output`/`-o`, and
from v0.48.0 it is `--format table|json|yaml|github-issue`. The shim in
Step 5a covers it.

### Checking the result

```bash
sbx version --json          # client.version == server.version == v0.49.2
sbx diagnose --json          # no version-mismatch, no versioned findings
sbx ls --json                # bare array, not {"sandboxes": [...]}
```

## What this does not cover

- **How to install a specific release.** The ledger records where releases are
  published (`docker/sbx-releases` on GitHub) and that nightlies are on the
  `sbx@nightly` Homebrew cask. It does not record the command that installs a
  pinned stable release. On this Mac the binary is a Homebrew cask from the
  `docker/tap` tap, so `brew upgrade sbx` after `brew trust --cask docker/tap/sbx`
  is the observed path — **not verified in the ledger**.
- **Downgrades.** The ledger does not describe downgrading. If you go back, the
  daemon may refuse to start; see `08-troubleshooting.md` for the state-wipe
  paths.
- **Cloud-only behaviour.** v0.45.0's allowlist change and v0.46.0's TCP fix
  are cloud-side. Local sandboxes are not affected.
- **The full release notes.** The table in Step 2 is the ledger's summary, not
  the release bodies. For the detail, read the release notes in
  `docker/sbx-releases`.
- **Every v0.48.0 change.** The flag and shape tables in ledger §3 and §4 are
  the complete set the ledger verified. Changes the ledger did not verify are
  not listed here.
