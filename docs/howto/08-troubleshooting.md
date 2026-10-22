# Troubleshooting

## What this is for

A symptom-to-fix reference. Find the symptom, check the cause, run the fix.

Two kinds of entry live here. Most are backed by
[`00-verified-facts.md`](00-verified-facts.md) and say "the ledger says" or
point at a section. The rest are marked **not verified in the ledger** — they
were observed in the v0.45.0-rc2 binary on this Mac, and you should treat
them as strong hints rather than guarantees.

## Before you start

```bash
# The three things you check first, every time.
sbx version --json
sbx diagnose --json
sbx policy log --json
```

`sbx diagnose` checks for a missing CLI binary, daemon reachability, a
CLI/daemon version mismatch, missing storage directories, broken
authentication, `mkfs.ext4`, running CLI and shim processes, an uninitialised
global network policy, and a ZFS setting that can slow image builds.

Save the output before you change anything. A diagnosis made after three
experiments is not a diagnosis.

```bash
sbx diagnose --json > diagnose-$(date -u +%Y%m%dT%H%M%SZ).json
```

On v0.45.0-rc2 the flag is `--output`/`-o`; from v0.48.0 it is `--format
table|json|yaml|github-issue`. `--json` works on both. Without a terminal,
`--upload` saves the bundle, skips the upload and exits 0 — **a successful
exit does not mean an upload happened**.

## 1. The quick table

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `sbx` cannot reach the daemon | The daemon is down, or the socket is stale | `sbx daemon restart`, then `sbx version --json` |
| `sbx ls` hangs, or reports a version mismatch | A stale CLI process or shim is holding the daemon | `sbx diagnose --json`; kill the reported process; `sbx daemon restart` |
| `sbx exec` never returns | A hung command, or a hung daemon | `sbx exec` has no timeout flag. Stop the sandbox: `sbx stop <name>` |
| A sandbox will not start after a downgrade | The daemon's datastore schema is newer than the binary supports | Go back up, or wipe the state (§7) |
| `sbx create` / `sbx run` fails on a missing directory | The workspace directory does not exist | `mkdir -p <path>` — the CLI prints this hint itself |
| The agent cannot install packages | The network policy denies the host | `sbx policy check network <host>`; `sbx policy allow` |
| A kit source is refused | `kit.allowedSources` does not cover it | Read, merge, then `sbx settings set kit.allowedSources '[...]'` |
| `ssh <name>.sbx` fails | No active Docker login, or no managed config | `sbx login`, then `sbx setup ssh` |
| Non-HTTP traffic fails | The proxy is HTTP-aware; L4 rules decide | `sbx policy log`; the `PROXY` column reads `network` |
| A host service is unreachable from the sandbox | The sandbox is not on the host's network | See §4 — **not verified in the ledger** |
| The agent cannot authenticate to a provider | No credential was injected | `sbx policy log`; read the `PROXY` column (§5) |
| An MCP stream stalls | HTTP/2 | Re-register with `--disable-http2` — **not verified in the ledger** |
| TLS fails against a self-signed CA | The CA is not in the sandbox's trust store | `sbx cp` the CA, then `update-ca-certificates` (§6) |
| `docker build` in the sandbox fails on a certificate | The build's image has no CA | Install the CA **in the image**, not the sandbox (§6) |
| Out of disk space in the sandbox | The volume is at its size limit | Raise the size and recreate (§3) |
| Slow file operations in a large direct-mode repo | The workspace mount is the bottleneck | Use clone mode (§3) — **not verified in the ledger** |
| "dubious ownership" from Git in clone mode | UID mismatch between the container and the mounted files | `git config --global --add safe.directory …` inside the sandbox — **not verified in the ledger** |
| Commits are not signed | The host Git identity is not set | Set `user.name` and `user.email` on the **host** |
| SSH is configured twice | A stale managed block in `~/.ssh/config` | Remove or rename the reported `Host *` block |

## 2. The daemon

### The daemon is unreachable

The daemon socket is `~/.sbx/run/d/sandboxd.sock`. `sbx version --json`
reports `server.state` **without** starting the daemon; `sbx ls` starts it.
That distinction matters: a command that "hangs" may just be a cold daemon.

```bash
sbx version --json          # look at server.state
ls -l ~/.sbx/run/d/sandboxd.sock
sbx daemon restart
```

If the socket file is present but the daemon does not answer, that is a
**stale socket**. `sbx daemon restart` clears it.

### The daemon will not start

```bash
sbx diagnose --json
```

`sbx diagnose` reports running CLI and shim processes (v0.49.1) and warns
when the global network policy is uninitialised. A CLI process that is still
running from an earlier session can hold the daemon. Kill what the report
names, then restart.

### A daemon that will not start after a downgrade

> **Not verified in the ledger.** Observed in the v0.45.0-rc2 binary, which
> carries a "no downgrade path from datastore schema X to Y" error and a
> "failed to start shim task after downgrading" error.

The daemon persists a datastore schema. A downgraded binary may not
understand the newer schema and will refuse to start. There is no supported
downgrade path in that direction.

Two ways out:

1. **Go back up.** Install the version the state was written by. The state is
   not the problem; the binary is.
2. **Wipe the state.** See §7. This deletes every sandbox, so do it only when
   you have decided to lose them.

### Corrupted daemon state

```bash
sbx daemon restart
```

If that is not enough, escalate to the state wipe in §7.

### A sandbox is "not responding"

```bash
sbx diagnose --json              # running CLI and shim processes
sbx stop my-sandbox              # a stop is the reconcile path
sbx run --name my-sandbox        # restart a stopped sandbox
```

There is no `sbx start` in the v0.45.0-rc2 command list. To bring a stopped
sandbox back, `sbx run --name my-sandbox` — that is what the stop message
itself tells you: "Restart with: sbx run --name my-sandbox".

### A hung command

`sbx exec` has no timeout flag. A command that hangs will hang the pipeline.

```bash
# From the host, in another terminal.
sbx stop my-sandbox
```

`sbx stop` is the safe interruption: it stops the sandbox and leaves the
state preserved. If you need a hard kill, `sbx rm --force my-sandbox`.

For a script, enforce the timeout yourself. `connectors/sbx-runner/sbx_runner.py`
does it at the subprocess boundary (300 s create, 900 s exec, 120 s remove)
and records the timeout in the receipt.

## 3. Sizes and slowness

### Out of disk space in a sandbox

The ledger records **one** disk-size setting:

| Key | Type | Default | Note |
| --- | --- | --- | --- |
| `sandbox.disk.dockerVolume` | string | `10g` | Size of a sandbox's `/var/lib/docker` volume. Minimum 512 MiB. |

```bash
sbx settings set sandbox.disk.dockerVolume 50g
```

The volume is created with the sandbox, so a bigger value applies to **new
sandboxes**. Raise it, then recreate.

> The **three size environment variables** are **not verified in the ledger**.
> Observed in the v0.45.0-rc2 binary:
>
> | Variable | Scope |
> | --- | --- |
> | `DOCKER_SANDBOXES_ROOT_SIZE` | The sandbox's root filesystem size. |
> | `DOCKER_SANDBOXES_DOCKER_SIZE` | The sandbox's `/var/lib/docker` volume size. |
> | `DOCKER_SANDBOXES_CLONED_WORKSPACE_SIZE` | The size of a clone-mode workspace. |
>
> They are daemon-side environment variables, read when the sandbox is
> created. Because they are not in the ledger, set them through
> `sandbox.disk.dockerVolume` where a setting exists, and treat the
> environment variables as an unverified escape hatch.

The two sizing flags at creation are separate from disk size:

```bash
sbx run claude . --cpus 4 --memory 8g
```

`--memory` has a minimum of 512 MiB, a default of 50% of host memory clamped
to 512 MiB–32 GiB, and a maximum of max(75% of host memory, 512 MiB).

### Slow filesystem operations in a large direct-mode repo

> **Not verified in the ledger.** The ledger records the *consequences* of
> direct mode (hard links reach outside the workspace; filesystem access
> policies do not block that) and records that `sbx diagnose` looks for "a
> ZFS setting that can slow image builds". It does not record a
> performance-tuning story.

The practical answer is the workspace mode, not a knob:

- **Use clone mode** for anything large. The agent works on an in-container
  copy; the host's filesystem is not on the hot path.
- **Check where the workspace lives.** A directory under a synced or
  network-backed folder (a cloud-sync client, a network mount, a deeply
  nested path) will be slow no matter what the sandbox does. The workshop's
  own fixtures live under a dedicated directory rather than in a synced
  folder.
- **Check `sbx diagnose --json`** for the ZFS finding. It affects image
  builds, not general file I/O.
- There is a `DOCKER_SANDBOXES_ENABLE_VIRTIOFS_CACHE` environment variable
  and a matching "virtiofs caching" setting surface in the v0.45.0-rc2
  binary. **Not verified in the ledger.** Do not flip it without a
  measurement.

## 4. A host service is unreachable from the sandbox

**This is a rule about what the sandbox is, not a bug.**

Each sandbox has **its own Docker Engine**. There is **no path to the host
daemon** (ledger §10). The sandbox is not on your host's loopback
interface: `localhost` inside the sandbox is the sandbox's own loopback, not
yours.

> The specific `host.docker.internal` and `localhost:PORT` mechanics are
> **not verified in the ledger**. The ledger states the boundary and stops
> there. The following is observed in the v0.45.0-rc2 binary on this Mac and
> should be confirmed on your host before you rely on it.

| You want the sandbox to reach | Do this |
| --- | --- |
| A service on the host's loopback, e.g. `localhost:3102` | Publish it to a host port and reach it from the sandbox through the host's non-loopback address. `localhost` inside the sandbox will not work. |
| A service on the host's LAN address | Reach it by that address, subject to the network policy. |
| A host-side tool | Put it behind a **host-side stdio MCP server** and register it with `sbx mcp add --command`. That is the supported path (ledger §10). |

Two rules that follow:

1. **`localhost:PORT` inside the sandbox is the sandbox's own loopback.** A
   service you published on the host is not reachable at `localhost` from
   inside.
2. **A host-side stdio MCP server is the sanctioned bridge.** It is also the
   one crossing of the hypervisor boundary, and it runs with your host user's
   full permissions — see `04-mcp-connectors.md` §7.

Check what the policy thinks before assuming it is a network problem:

```bash
sbx policy check network api.github.com
sbx policy log --json
```

## 5. The agent cannot authenticate

**The `PROXY` column of `sbx policy log` tells you what happened.** From
ledger §10:

| `PROXY` value | Meaning | What to do |
| --- | --- | --- |
| `forward` | A credential was injected | The credential was injected. A 401 is upstream of the proxy — check the key, its scope, its quota. |
| `forward-bypass` | No injection happened | The agent had no credential to inject. Import or store one. |
| `transparent` | Policy was enforced, no injection | The request was allowed through untouched. A failure here is the provider's. |
| `network` | Non-HTTP traffic | Not an HTTP request. See §6. |
| `browser-open` | A browser open | The sandbox tried to open a host browser. |

```bash
sbx policy log --json
sbx policy log my-sandbox --json
sbx policy log --limit 20 --json
```

### The credential is missing

```bash
sbx secret ls
sbx secret import --all          # imports every provider credential found in the CI environment
```

API keys are injected into HTTP headers by the host proxy and **never stored
in the VM** (ledger §10). So a key missing from the secret store cannot be
recovered from inside the sandbox.

### The credential is present but the request is not proxied

OAuth responses are intercepted and the real token is replaced with a
sentinel. `passthrough: true` opts out. A mismatch between a provider's
expectations and the interception is a provider-specific problem, and the
`PROXY` column is how you tell interception from non-interception.

### The agent's own CLI login failed

A separate thing from provider credentials. An agent CLI that wants its own
login needs a browser, and the sandbox does not have one:
`browser.enabled` (v0.49.1) controls whether sandboxed processes can open the
host browser at all, and setting it to `false` also prevents in-sandbox OAuth
logins that redirect to `localhost`.

```bash
sbx settings set browser.enabled true
```

### Non-HTTP traffic fails

The proxy is HTTP-aware. A raw TCP or UDP connection is decided at the
network (L4) layer, where the rules are whole-destination:
`connect:tcp`, `connect:udp`.

```bash
sbx policy allow --help
sbx policy check network api.github.com
```

Known open bug (ledger §17): **UDP to a denied hostname can be permitted via
an allow rule for that hostname's resolved IP, when multiple hostnames are
tracked.** v0.46.0 fixed the TCP case only. If a UDP path behaves
inconsistently with the policy, this is why.

The `PROXY` column reads `network` for these requests.

## 6. Certificates in the sandbox

### A self-signed or corporate CA

The sandbox runs behind a proxy that intercepts TLS. The CA that signs the
intercepted certificates has to be in the sandbox's trust store, or every
HTTPS call fails with a certificate error.

The recipe:

```bash
# 1. Put the CA on the host, somewhere sbx can read it.
ls -l /path/to/corporate-ca.pem

# 2. Copy it into the sandbox.
sbx cp /path/to/corporate-ca.pem my-sandbox:/tmp/corporate-ca.pem

# 3. Install it into the trust store.
sbx exec my-sandbox -- sudo cp /tmp/corporate-ca.pem /usr/local/share/ca-certificates/corporate-ca.pem
sbx exec my-sandbox -- sudo update-ca-certificates

# 4. Clean up.
sbx exec my-sandbox -- rm -f /tmp/corporate-ca.pem
```

`sbx cp SRC DST` takes `SANDBOX:PATH` on one side (ledger §15). Copying
between two sandboxes is not supported. A directory is placed at the
destination; if the destination exists as a directory the source goes inside
it. From v0.49.1 a missing destination directory is reported without extra
tar errors — on v0.45.0-rc2, create the destination directory yourself.

> The `update-ca-certificates` step is **not verified in the ledger** as a
> recipe. What the ledger does verify: `sbx cp`'s semantics (ledger §15), and
> that the same package is the one to upgrade in place after the eMudhra CA
> compromise (ledger §9):
>
> ```bash
> sbx exec my-sandbox -- sudo apt-get update
> sbx exec my-sandbox -- sudo apt-get install --only-upgrade -y ca-certificates
> ```

### Do not override `SSL_CERT_FILE`

> **Not verified in the ledger.** Observed in the v0.45.0-rc2 binary, which
> sets `REQUESTS_CA_BUNDLE` and `CURL_CA_BUNDLE` to
> `/etc/ssl/certs/ca-certificates.crt` for processes it starts.

Pointing `SSL_CERT_FILE`, `CURL_CA_BUNDLE` or `REQUESTS_CA_BUNDLE` at your
own PEM file replaces the whole trust store. That store is what makes the
**proxy's** certificate trusted, not just your own server's. Overriding it
breaks every HTTPS call in the sandbox, including the ones that were
working.

Install your CA **into** the system store with `update-ca-certificates`. Do
not replace the store.

### A certificate error during `docker build` in the sandbox

The sandbox has its own Docker Engine, and `docker build` runs a build in a
**fresh image** whose filesystem is the image's, not the sandbox's. So
installing the CA into the sandbox does not help the build.

> **Not verified in the ledger.** This follows from what the ledger does say
> — each sandbox has its own Docker Engine (ledger §10) — plus the ordinary
> semantics of a container build.

The options, in order of preference:

1. **Use a base image that already trusts the CA.** The corporate CA baked
   into the base image is the clean fix.
2. **Install the CA as the first thing in the Dockerfile**, before the step
   that needs the network:

   ```dockerfile
   COPY corporate-ca.pem /usr/local/share/ca-certificates/corporate-ca.pem
   RUN update-ca-certificates
   ```

3. **Pass the bundle explicitly to the tool that needs it** for the duration
   of that one step, rather than exporting it globally.

Do not reach for `tls.allowNegativeSerial` for this. It relaxes TLS
validation **for negative-serial certificates only** (ledger §7). It is not a
general "trust the bad certificate" switch.

## 7. The last-resort state wipe

Ordered from least to most destructive. Try them in order.

### 1. Restart the daemon

```bash
sbx daemon restart
```

### 2. Reset everything

```bash
sbx reset --force
```

`sbx reset` is the portable answer on every platform. It:

- stops all running sandboxes gracefully (30 s timeout),
- clears the image cache,
- clears all internal registries,
- deletes all sandbox state,
- removes all policies,
- removes the managed SSH configuration,
- clears the assistant's sessions and history,
- deletes all stored secrets,
- signs out of Docker Sandboxes,
- stops the daemon,
- removes all state, cache and config directories.

It is destructive and cannot be undone. Running agents are terminated and
their work is lost. Cached images are deleted and recreated on next use.
Stored secrets need re-entering.

| Flag | Effect |
| --- | --- |
| `--preserve-secrets` | Keep stored secrets. |
| `--force` | Skip the confirmation prompt. |

**`sbx reset` also clears the shared skills store** (ledger §12).

### 3. Remove the state directories by hand

Only after `sbx reset` has failed or been refused.

**macOS** — the paths observed on this Mac:

| Path | Holds |
| --- | --- |
| `~/.sbx/` | The daemon's run directory, including `run/d/sandboxd.sock`. |
| `~/Library/Application Support/com.docker.sandboxes/` | The state root, including `sandboxes/` (the daemon state, `sandboxd/`, `ssh/`, `env/`, `locks/`, `settings.json.lock`, `first-login.json`) and `sandboxes/agent-skills` (the shared skills store). |

```bash
sbx daemon stop 2>/dev/null || true
rm -rf ~/.sbx
rm -rf "$HOME/Library/Application Support/com.docker.sandboxes"
```

**Linux and Windows** — **not verified in the ledger.** The ledger gives the
macOS paths only. Use `sbx reset` as the supported answer, and if you must
remove directories by hand, find them from the socket path the CLI prints in
its error messages rather than guessing.

> The Linux and Windows state paths are **not in the ledger** and are **not
> recorded here**. If you discover them, add them to
> `00-verified-facts.md` — that is exactly what the ledger is for.

## 8. MCP-specific problems

### A stream stalls over HTTP/2

> **Not verified in the ledger.** Observed in the v0.45.0-rc2 binary.

`sbx mcp add` has a `--disable-http2` flag: "Do not negotiate HTTP/2 for this
remote server, leaving HTTP/1.1." It applies to `--url` servers.

Re-register the server with the flag:

```bash
sbx mcp rm my-server
sbx mcp add my-server --url https://mcp.example.com/mcp --disable-http2
sbx mcp load my-server --sandbox my-sbx
```

The gateway is an HTTP server between the agent and the remote MCP server.
When a stream works with one server and not another, the transport is the
first thing to change.

### A server is registered but its tools never appear

```bash
sbx mcp ls
sbx mcp inspect my-server
```

`sbx mcp ls` reports the gateway that serves each server, the transport, and
whether it is usable now. A server that needs authorisation carries the
`sbx mcp auth` next step. A server with a custom header whose placeholder
has no stored value carries the `sbx secret set` next step.

```bash
sbx mcp auth status --all --format json
sbx mcp auth my-server
```

### An MCP call is refused by policy

MCP policies are Cedar policies, default-deny, with their own actions
(`register`, `invokeTool`, `invokePrimordial`, `readResource`, `getPrompt`)
and resources (`MCP::Server`, `MCP::Tool`, `MCP::Resource`, `MCP::Prompt`,
`MCP::Primordial`).

`@requireApproval("reason")` uses MCP elicitation: the gateway asks the
originating client, then re-evaluates with an approval digest. It is
per-request and in-session — categorically different from a network
approval, which records a durable local rule.

## 9. Git problems

### "dubious ownership" in clone mode

> **Not verified in the ledger**, and the WSL-specific trigger is not
> either. The mechanism is Git's own `safe.directory` check, and the
> v0.45.0-rc2 binary does carry a `safe.directory` key.

Clone mode mounts the source read-only at `/run/sandbox/source` and runs a
git-daemon inside the sandbox. If the container's Git sees files owned by a
different UID than the one it runs as, it refuses to work with them:

```
fatal: detected dubious ownership in repository at '/run/sandbox/source'
```

Fix it **inside the sandbox**, not on the host:

```bash
sbx exec my-sandbox -- git config --global --add safe.directory /run/sandbox/source
```

Or, for a sandbox that handles several checkouts:

```bash
sbx exec my-sandbox -- git config --global --add safe.directory '*'
```

Use the narrow form where you can. `'*'` disables the check for everything
the sandbox can see, which is a larger grant than the problem needs.

### Commits are not signed, or the identity is wrong

`com.docker.sandbox/git-identity@1` (v0.48.0) needs `user.name` and
`user.email` in the **host** Git config. Set them there:

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

The capability provides an **identity**, not a signing key. GPG and SSH
commit signing inside the sandbox are not covered by the ledger.

### The agent cannot push

In clone mode the `sandbox-<name>` remote is served by a git-daemon inside
the sandbox, bound to localhost on the host. `sbx stop` stops it; a restart
assigns a new ephemeral port and the CLI rewrites the remote URL; `sbx rm`
removes the remote entry.

If a push fails, check the remote URL rather than the branch:

```bash
sbx exec my-sandbox -- git remote -v
```

Also: **local-path remotes are not copied into the clone.** If `origin` was
`file://…` or a filesystem path, the clone does not have it.

And: **clone mode is rejected from inside a Git worktree other than the main
one.**

## 10. When to escalate

```bash
sbx diagnose --upload
```

This prints a diagnostics ID. Without a terminal it saves the bundle, skips
the upload and exits 0 — so check for the ID, do not trust the exit code.

Bugs go to `github.com/docker/sbx-releases/issues`.

Include the `sbx version --json` output, the `sbx diagnose --json` output,
and the exact command that failed. A receipt from
`connectors/sbx-runner/sbx_runner.py` is even better: it names the build, the
command, the exit code and the digests of the inputs.

## What this does not cover

- **Anything the ledger does not record.** Where this guide says "not
  verified in the ledger", the fact came from the v0.45.0-rc2 binary's own
  strings and help text on this Mac, or from a general understanding of the
  underlying technology. Confirm before you depend on it.
- **The Linux and Windows state paths.** Only the macOS paths are given, and
  only because they are the only ones the ledger records.
- **Provider-specific failures.** The `PROXY` column tells you whether the
  proxy injected a credential. It does not tell you that your Anthropic key
  is out of quota.
- **The `sbx tui` interface.** It exists in the v0.45.0-rc2 binary and is
  not described in the ledger.
- **Cloud-mode differences.** Everything here is local. Cloud sandboxes
  expire on a TTL, have no host workspace, and are diagnosed through
  `--cloud`.
