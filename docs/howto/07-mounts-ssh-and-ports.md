# Mounts, SSH and published ports

## What this is for

Three runtime surfaces that let a sandbox reach out and let you reach in:
`sbx mount` and `sbx umount` for host paths, `sbx setup ssh` for a login-free
SSH connection, and `sbx ports` for reaching a service running inside the
sandbox.

All three have one thing in common: they are governed. A mount is checked
against the filesystem policy before the call reaches the daemon. An SSH
connection is refused without an active Docker login. A published port binds
on loopback unless you say otherwise.

## Before you start

- The `sbx` CLI on your `PATH` and a Docker login: `sbx login`.
- A running sandbox for the runtime operations.
- Read [`00-verified-facts.md`](00-verified-facts.md) §13 (runtime mounts) and
  §14 (SSH and editor integrations).

```bash
sbx version --json
sbx ls
```

## 1. Runtime mounts

### The command

```
sbx mount SANDBOX HOST[:CTR_TARGET[:ro|rw]]
sbx umount SANDBOX HOST[:CTR_TARGET]
```

`HOST` alone mounts at the same absolute path inside the container — the same
convention the create-time workspace mount uses, so in-container paths match
host paths.

`HOST:CTR_TARGET` is a **real bind mount** (setns + open_tree + move_mount),
not a symlink.

`HOST:CTR_TARGET:ro|rw` sets the mode. `rw` is the default.

```bash
# Same path inside and out.
sbx mount my-sandbox /Users/me/extra-data

# At a different path inside the container, read-write.
sbx mount my-sandbox /Users/me/extra-data:/workspace/data

# Read-only. Writes inside the container return EROFS.
sbx mount my-sandbox /Users/me/extra-data:/workspace/data:ro
```

### The rules that surprise people

| Rule | Detail |
| --- | --- |
| `HOST` must exist on the host | The command fails if it does not. |
| `CTR_TARGET` must be absolute | A relative target is rejected. |
| Relative `HOST` is resolved before submission | Against the current working directory, and `./..` segments are cleaned. |
| Mounting is **idempotent** | Re-running the same mount spec is a no-op success, not an error and not a second mount. |
| Mounts are **restored after stop and start** | See below. |
| The **mount policy is checked before the call reaches the daemon** | Mirroring the create-time check. |

### Idempotence

Re-running the same `MOUNT_SPEC` is a no-op success. That matters for scripts:
you do not need to track what you already mounted, and a repeated mount does
not stack.

The asymmetry to remember: `sbx umount` is **not** idempotent in the same
way. If `CTR_TARGET` is neither mounted nor saved, the command **fails**
without revoking access to `HOST`. It is telling you that your spec does not
match the mount you are trying to revoke, rather than silently succeeding.

### Restore on restart

Mounts are restored after a stop and a start. If a saved mount cannot be
restored, **startup fails**. Two ways out:

- Restore the host path — put the directory back where it was.
- Remove the saved mount while the sandbox is stopped:

```bash
sbx stop my-sandbox
sbx umount my-sandbox /Users/me/extra-data
```

For a stopped sandbox, `umount` removes the **saved** mount so it will not be
restored on the next start. The sandbox does not need to be started first.

This is the trap: if you delete a host directory out from under a mounted
sandbox and then restart the sandbox, the sandbox will not come back. Unmount
it first.

### The policy check

The mount policy is checked **before the call reaches the daemon**, mirroring
the create-time check, and it uses the **effective read-only intent**. So a
policy that distinguishes `ro` from `rw` matches what the bind mount actually
does, rather than the intent you asked for.

Filesystem policies have two actions: `read` and `write`. A writable mount
needs both; a read-only mount needs only `read`.

One important limit: filesystem policies are enforced at **workspace mount
time** — that is, at sandbox creation — and not on later `sbx mount` calls
(ledger §5). A runtime `sbx mount` is checked against the **mount policy**
before it reaches the daemon, but the workspace's own read/write policy is
not re-evaluated at runtime. Do not rely on a creation-time filesystem policy
to constrain a later runtime mount.

### Revoking

```bash
sbx umount my-sandbox /Users/me/extra-data
```

- `CTR_TARGET` defaults to the host path, the same default `sbx mount` uses.
  So `sbx umount SANDBOX HOST` undoes `sbx mount SANDBOX HOST`.
- A mount created with an **explicit** `CTR_TARGET` must be unmounted with
  the same target:

```bash
sbx umount my-sandbox /Users/me/extra-data:/workspace/data
```

- The host path **need not still exist**. The allowlist is the source of
  truth, so a path deleted after mounting can still be revoked.
- A successful unmount revokes access through **every** mount of the same
  `HOST`. Other mounts remain in place but lose access to that host path.

## 2. SSH

### `sbx setup ssh`

```bash
sbx setup ssh
ssh my-sandbox.sbx -- echo hello
```

This writes a managed block to `~/.ssh/config` (plus a generated
`known_hosts`), so `ssh <name>.sbx` connects with no prompts. Named app
instances use `ssh <name>.sbx-<app>`.

The command is **idempotent**: re-run it any time.

| Flag | Meaning |
| --- | --- |
| `--alias <pattern>` | The `ssh_config` Host pattern to write. Default `*.sbx`. |

`sbx setup ssh remove` removes the SSH client config for the current local app
instance.

### What the managed config actually does

This is the part people get wrong. There is **no SSH server inside the
sandbox** and **no SSH key**.

| Thing | Reality |
| --- | --- |
| `Host *.sbx` | The managed block's host pattern. |
| `ProxyCommand` | Relays the connection to the daemon over its **Unix socket**. |
| `IdentityAgent none` | No agent is used. |
| `IdentityFile /dev/null` | No key is used. |

So the SSH client is not authenticating to a machine in the sandbox. It is
handing the connection to the local daemon, and the daemon is opening the
session.

**The daemon accepts a connection only while you hold an active Docker
login.** Sign in first (`sbx login`); after `sbx logout`, `ssh <name>.sbx`
stops working, and no key would have helped.

Two more behaviours:

- **SSH sessions start in the sandbox workspace**, matching `sbx exec`.
- **SSH clients can forward a port to a loopback address inside the sandbox.**
  So `ssh -L 8080:127.0.0.1:8080 my-sandbox.sbx` gives you a local port that
  reaches a service on the sandbox's loopback interface.

### `sbx setup ssh --auto-create`

**v0.48.0 and later.** Registers connections in **Codex** and **Claude
Desktop**, on macOS and Windows.

Each connection creates a **dedicated sandbox on first use**, with **no host
workspace mounted**. Clone the project in, or copy it in.

Remove them with:

```bash
sbx setup ssh remove
```

> The `--auto-create` flag is **not in the v0.45.0-rc2 binary on this Mac**.
> On the installed version, `sbx setup ssh --auto-create` fails with an
> unknown-flag error. Upgrade before using it, or use `sbx setup ssh` with
> your own client.

### Editor integrations

VS Code, Cursor, Claude Desktop, ChatGPT, T3 Code and Orca are supported as
editor integrations. The ledger does not record the configuration steps for
each one.

## 3. Published ports

### The command

```
sbx ports SANDBOX [--publish <spec>] [--unpublish <spec>] [--json]
```

The port spec is:

```
[[HOST_IP:]HOST_PORT:]SANDBOX_PORT[/PROTOCOL]
```

```bash
sbx ports my-sandbox                                  # list
sbx ports my-sandbox --publish 8080                   # ephemeral host port
sbx ports my-sandbox --publish 3000:8080              # fixed host port
sbx ports my-sandbox --unpublish 3000:8080
```

**Publishing a local port starts a stopped sandbox** before creating the host
binding. You do not need to start it yourself.

### The loopback default

**If `HOST_IP` is omitted, the port is bound on loopback.** That is the safe
default, and it is why a published sandbox service is not reachable from
another machine on your network.

The address family depends on the protocol and on the sandbox's address
families:

| Protocol | Binds |
| --- | --- |
| `tcp`, `udp` | Both `127.0.0.1` and `::1` (only `127.0.0.1` if the sandbox is IPv4-only) |
| `tcp4`, `udp4` | Only `127.0.0.1` |
| `tcp6`, `udp6` | Only `::1` |

### The protocol suffixes

Supported protocols: `tcp`, `tcp4`, `tcp6`, `udp`, `udp4`, `udp6`.

**When publishing without a protocol, `tcp4` is used** — so a sandbox service
listening only on IPv4 is reachable without a host client having to avoid
`::1`. The exception is `tcp6` when `HOST_IP` is an IPv6 address. Publish
`tcp` explicitly to bind both families.

```bash
sbx ports my-sandbox --publish 8080/tcp
```

When **un**publishing without a protocol, the mapping is removed whether it
was published with that same default or as dual-stack `tcp`. Name the
protocol to remove a `tcp6` or `udp` mapping; **anything left behind is
reported**.

### Listening on all interfaces

The default is loopback. To listen on all interfaces you must **say so
explicitly** by naming `HOST_IP`:

```bash
sbx ports my-sandbox --publish 0.0.0.0:8080:8080
```

Do this only when you have a reason. A published port on all interfaces is a
service reachable from the network your host is on, and the sandbox's own
network policy does not govern inbound host connections the way it governs
the sandbox's outbound traffic.

For a local-only preview, keep the default. The workshop's
`factory/sbxenv.yaml` does exactly that — `hostIP: 127.0.0.1` for port 3102
— stated explicitly rather than left to the default.

### `sbx run --publish` and re-attaching

```bash
sbx run claude . --publish 3000:8080
```

`--publish` is **applied when the sandbox is created** and **ignored when
re-attaching**. The CLI says so: "Applied when the sandbox is created; ignored
when re-attaching (use `sbx ports`)".

So a script that runs `sbx run --publish …` to re-attach to an existing
sandbox and expects the port to be there is wrong. Use `sbx ports` to publish
after the fact — that is the command for it, and it works on a running
sandbox.

### The output shape

From v0.48.0 (ledger §4):

| Before | After |
| --- | --- |
| `sbx ports` (cloud): `port` | cloud: `sandbox_port` + `url` |
| `sbx ports` (local) | `host_ip`, `host_port`, `sandbox_port`, `protocol` |
| `sbx ports --publish/--unpublish`: human-readable lines | result rows with `action: published` / `action: unpublished` |

```bash
sbx ports my-sandbox --json
```

### Surviving a restart, and the ephemeral-port trap

> The ledger does **not** record the restart behaviour of published ports.
> **Not verified in the ledger.** What is observed in the v0.45.0-rc2 binary
> on this Mac is a persisted *desired published ports* state that is loaded
> when the sandbox starts, and the ledger separately records that in clone
> mode a restart assigns a **new ephemeral port** and the CLI rewrites the
> remote URL.
>
> The safe operational rule, which holds either way: **if the host port was
> OS-assigned (ephemeral), do not cache the host port number across a
> restart.** Read it back:

```bash
sbx ports my-sandbox --json | jq -r '.[] | "\(.host_ip):\(.host_port) -> \(.sandbox_port)/\(.protocol)"'
```

A **fixed** host port is stable. An **ephemeral** one is not guaranteed to
be. If a script needs a stable URL, publish a fixed host port.

### Cloud ports

Under `--cloud` the spec is `SANDBOX_PORT[/tcp]` only. **UDP and host
bindings are refused.** The cloud control plane assigns a publicly reachable
URL for each exposed port, which is a different security posture from the
local loopback default: a cloud sandbox's exposed port is public.

## 4. Putting it together

A sandbox with a read-only data mount, an SSH entry, and one published
preview port:

```bash
sbx run claude . --publish 3000:8080

sbx mount my-sandbox /Users/me/reference-data:/workspace/reference:ro
sbx setup ssh
sbx ports my-sandbox --json
```

The teardown, in the order that avoids a failed restart:

```bash
sbx umount my-sandbox /Users/me/reference-data:/workspace/reference
sbx rm --force my-sandbox
```

Unmount before you delete the host directory, and before you remove the
sandbox. A saved mount that cannot be restored fails the next start.

## What this does not cover

- **The filesystem policy authoring surface.** The `read` and `write` actions
  and the mount policy are described in ledger §5. How to write one is not
  covered here.
- **The `sbx env` YAML equivalents.** `factory/sbxenv.yaml` declares
  `ports:` with `sandbox`, `host`, `protocol` and `hostIP`. The mapping
  between that block and the `sbx ports` spec is not in the ledger.
- **Editor-integration configuration.** The supported list is in ledger §14;
  the setup steps for each editor are not.
- **`sbx setup ssh --auto-create` details.** That it exists in v0.48.0 is in
  the ledger. Which sandbox template it uses, and how it names the
  connections, are **not verified in the ledger**.
- **Windows and Linux paths.** The mount rules here are platform-neutral, but
  the host paths in the examples are macOS-shaped.
- **Published-port behaviour across a downgrade.** The ledger does not
  describe it, and the persisted port state is versioned with the daemon.
