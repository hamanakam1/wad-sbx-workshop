# Verified facts: Docker Sandboxes as of 2026-10-22

Every row here was checked against a primary source, not recalled. The
"Checked against" column says which. Anything not in this file is not
treated as fact by the how-to guides in this directory.

Three sources were used:

- **RELEASE** — the release body of `docker/sbx-releases` on GitHub.
- **HELP** — the `--help` output of the `sbx` binary installed on this Mac.
- **DOCS** — a page on `docs.docker.com`.

> The installed binary is **v0.45.0-rc2**, which predates three minor
> releases. Anything marked "0.48+" does not exist in the local binary.
> That gap is the reason this file exists.

## 1. Versions

| Release | Date | What it changed that matters |
| --- | --- | --- |
| v0.45.0 | 2026-09-21 | Cloud hostname allowlists no longer gain implicit `0.0.0.0/0` and `::/0` rules. |
| v0.46.0 | 2026-09 | Fixed raw TCP to a denied hostname being permitted by an allow rule for that hostname's resolved IP. The UDP case with multiple tracked hostnames is still open. |
| v0.47.0 | 2026-10-05 | `sbx run --rm`; kit capability `com.docker.sandbox/long-running@1`; `sbx kit sign` / `sbx kit push --sign` work on registries that refuse manifest deletion (GHCR, Docker Hub); OAuth token interception fixed for capitalised and trailing-dot hostnames; the proxy rejects unrecognised OAuth grants; kit pulls enforce blob, decompressed-size and entry-count limits. |
| v0.48.0 | 2026-10-13 | **Breaking**: flag renames and JSON shape changes (section 3 and 4). Also `sbx setup ssh --auto-create` for Codex and Claude Desktop; v3 kit capability groups; `ssh-agent@1` and `git-identity@1`; `sbx kit add` with startup commands; `SBX_AGENT`; `sbx mcp unload`; `sbx run --model --provider`; `sbx run --variant`. |
| v0.49.0 | — | Skipped. |
| v0.49.1 | 2026-10-20 | `sbx kit validate|inspect --offline`; `kit.allowedSources` and `kit.trustedSigners` structure checks before saving; `browser.enabled`, `daemon.log.*` settings; `sbx policy refresh`; `sbx cp` reports a missing destination directory; `sbx diagnose` reports running CLI and shim processes and warns when the global network policy is uninitialised; explicit CIDR deny beats a matching domain allow; full templates gain a C/C++ toolchain. |
| v0.49.2 | 2026-10-21 | Response to the **eMudhra CA compromise**: Go 1.27.3, bundled llmman 0.1.571, rebuilt built-in template images. The CLI update alone does not update existing sandboxes. |

Checked against: RELEASE (docker/sbx-releases release bodies), DOCS
(`/ai/sandboxes/workflows/automation/`, "Update scripts for v0.48.0").

### Nightlies

A nightly build is published every day under the `nightly-*` tag
(`sbx@nightly` Homebrew cask). Nightlies are prereleases and carry
provenance and SBOM assets. They are not covered by the table above.

## 2. What the local binary actually has

`sbx version --json` on this Mac:

```json
{"client": {"version": "v0.45.0-rc2", "revision": "4db7091451a820a472944415bbb0b3ac8bb04701", "build_tags": "cloud"},
 "server":  {"state": "running", "version": "v0.45.0-rc2", "revision": "4db7091451a820a472944415bbb0b3ac8bb04701", "api_version": "0.36.0"}}
```

The daemon socket is `~/.sbx/run/d/sandboxd.sock`. `sbx version --json`
reports `server.state` without starting the daemon; `sbx ls` starts it.

Commands present in v0.45.0-rc2 that the older workshop chapters never
mention: `attach`, `cp`, `exec`, `mount`, `umount`, `move`, `setup`,
`skills`, `ssh`, `tui`, `volume`, `ttl`, `reset`, `diagnose`, plus
`sbx kit` subcommands `pack`, `push`, `pull`, `sign`, `verify`,
`provenance`, `builder`, and `sbx mcp` subcommands `catalog`, `auth`,
`load`.

Checked against: HELP.

## 3. Flag changes in v0.48.0

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

Also: `--format`, `--json` and `-q` are mutually exclusive on commands with
shared output flags — even `--json --format json` is rejected. `sbx
settings ls` is primary; `sbx settings list` stays an alias. `sbx kit
builder history rm` keeps `--yes`/`-y` because its `--force` is forwarded to
Buildx. The deprecated `--global` flag on secret commands is still accepted
but hidden and warns on stderr.

Checked against: RELEASE (v0.48.0), DOCS (automation page).

## 4. Structured output changes in v0.48.0

`--json` is an alias for `--format json`. Field names are `snake_case`; YAML
is the same representation.

| Command | Old shape | New shape |
| --- | --- | --- |
| `sbx ls` | `{"sandboxes": [...]}` | bare array; rows carry `created_at`, `last_used_at` (RFC 3339) and `detached` |
| `sbx template ls` | `{"images": [...]}` | bare array |
| `sbx mcp ls` (local) | `{"gateway": {...}, "servers": [...]}` | bare array; each row carries `gateway` |
| `sbx policy log` | `allowed_hosts` / `blocked_hosts` | `{"updated_at", "allowed": [...], "blocked": [...]}`; entries carry `sandbox_id`, `host`, `proxy_type`, `rule`, `reason`, `count`, `first_seen`, `last_seen` |
| `sbx --cloud policy ls` | camelCase | snake_case |
| `sbx ports` | `port` (cloud) | cloud: `sandbox_port` + `url`; local: `host_ip`, `host_port`, `sandbox_port`, `protocol` |
| `sbx ports --publish/--unpublish` | human-readable lines | result rows with `action: published` / `action: unpublished` |
| `sbx volume create\|ls\|inspect` (cloud) | `ID`, `Name`, `CreatedAt` | `id`, `name`, `created_at`; `inspect` defaults to a human view, so ask for `--format json` |
| `sbx rm`, `sbx stop`, `sbx template rm`, `sbx volume rm` | one line per target | array of `{action, resource, name, id?, error?}`; actions are lowercase `removed`, `stopped`, `removing`, `stopping`, `failed` |

`.sandboxes[].name` becomes `.[].name`.

**Streams.** Results go to stdout. Errors, warnings, corrective hints,
daemon-start progress, image pulls and kit-build progress go to **stderr**.
A script that captures only stdout will miss the reason a command failed.

**Non-interactive behaviour.** A confirmation prompt needs a terminal.
`echo y | sbx secret set …` no longer confirms. A command needing
confirmation exits 1 without a TTY. Declining returns non-zero. `sbx
create` and `sbx run` fail when the workspace directory does not exist and
print a `mkdir -p` hint. Ctrl-D cancels rather than accepting the default.

Checked against: RELEASE (v0.48.0), DOCS (automation page).

## 5. Network policy and governance

### Presets and layers

- Presets: `Open`, `Balanced` (the recommended default: deny by default,
  common dev sites allowed), `Locked Down`.
- Deny always beats allow, across every layer and every composed kit.
- Under **organisation governance**, only organisation allow rules grant
  access. Local allow rules and **kit-defined allow rules are inactive**;
  deny rules from every source still apply. Organisation policies are
  default-deny.
- Limits: 100 policies per organisation, 250 rules per policy, 400 KB total.
- Organisation changes propagate to developer machines in up to 5 minutes.
  `sbx policy refresh` (v0.49.1) refreshes immediately without restarting
  the daemon; existing connections stay open, so restart the sandbox to
  close connections the new policy denies.
- `sbx policy reset` forces an immediate pull but **deletes every local
  rule, including recorded approvals**.

### Approvals

`sbx policy approval ls|inspect|respond` exists in v0.45.0-rc2. Without
organisation governance, a request matching no rule **asks for approval**
rather than being denied outright. The blocked request is denied
immediately; the approval affects later requests, so an agent that retries
picks it up next time. An approval records one destination scoped to the
asking sandbox. Approved destinations appear in `sbx policy ls --wide
--created-via approval` and are wiped by `sbx policy reset`.

### Layers

- **Network (L4)**: whole-destination rules. `connect:tcp`, `connect:udp`.
- **HTTP (L7)**: method plus path patterns, one destination per rule. A
  network deny is a floor an HTTP allow cannot raise. `sbx policy log`
  reports the host-level decision and does **not** evaluate methods or
  paths, so it can differ from the decision for a specific method and path.
- **Filesystem**: actions `read` and `write`. A writable mount needs both;
  read-only needs read. Enforced **only at workspace mount time**, that is,
  at sandbox creation — not on later `sbx mount` calls.
- **MCP**: Cedar policies, default-deny, with their own actions
  (`register`, `invokeTool`, `invokePrimordial`, `readResource`,
  `getPrompt`) and resources (`MCP::Server`, `MCP::Tool`, `MCP::Resource`,
  `MCP::Prompt`, `MCP::Primordial`).
  `@requireApproval("reason")` uses MCP elicitation: the gateway asks the
  originating client, then re-evaluates with an approval digest. It is
  per-request and in-session, categorically different from a network
  approval, which records a durable local rule.

Checked against: DOCS (governance pages), HELP.

## 6. Kits

### v2 versus v3

| | v2 | v3 |
| --- | --- | --- |
| Descriptor | `spec.yaml`, `schemaVersion: "2"` | YAML with `# syntax=docker/sandbox-kit:3` |
| Kinds | `sandbox`, `mixin` | `workload`, `mixin`, plus kit sets |
| Composition | `extends:`, `mixins:` | `requires:`, `provides:`, `integrates:`, `conflicts:` |
| Build | `sandbox.build` (runtime pending) | Dockerfile recipe, built with **Buildx** |
| Minimum sbx | v0.36 | v0.45 |
| Published as | ZIP or OCI | OCI image |
| Signable | local dir, Git, OCI (a **ZIP cannot carry a verifiable signature**) | **OCI image only** |

v3 kits cannot compose with v1 or v2. The built-in agent names (`claude`,
`codex`, …) still select **v2** kits, so `sbx run claude --kit ./some-v3-mixin`
fails. `sbx kit pack/push/pull` are v1/v2 tooling; v3 kits are published with
`docker buildx build`.

### v2 pattern enforcement

Enforced: an exact host, `host:port`, `*.example.com`.
Parsed but **enforcement pending**: `**.example.com`, port ranges, a port
wildcard, CIDR.

### Capability groups (v3, v0.48.0+)

A group is selected only if the runtime can provide every capability in it.
An unavailable capability in an **optional** group skips the group **in
full — including its network rules, setup commands, generated files and
agent instructions**. An unavailable capability in a **required** group
fails sandbox creation.

`com.docker.sandbox/long-running@1` (v0.47.0) is config-less: it must not
carry a `config` key, not even empty or null. Cloud sandboxes cannot
provide it.

`com.docker.sandbox/ssh-agent@1` (v0.48.0) needs SSH forwarding enabled and
a host agent. `com.docker.sandbox/git-identity@1` (v0.48.0) needs
`user.name` and `user.email` in the **host** Git config.

### Supply chain commands

```
sbx kit pack DIRECTORY -o <file.zip>
sbx kit push DIRECTORY REFERENCE --sign
sbx kit pull REFERENCE --output <FILE>
sbx kit sign ./my-kit/
sbx kit sign --key cosign.key ./my-kit/
sbx kit verify --certificate-identity user@example.com \
      --certificate-oidc-issuer https://accounts.google.com ./my-kit/
sbx kit verify --key cosign.pub ./my-kit/
sbx kit provenance ghcr.io/myorg/my-kit:1.0
sbx kit validate <path> [--json] [--kit-arg ...] [--kit-args-file ...]
sbx kit inspect  <path> [--json] [--kit-arg ...] [--kit-args-file ...]
sbx kit add SANDBOX REFERENCE
```

Sigstore and cosign compatible; keyless by default. **Provenance is unsigned
by default** — `sbx kit push --sign` signs the manifest and its provenance
together. A signature covers `spec.yaml` and `files/`; it does **not** pin
image tags or verify bytes that an install command downloads.

### Trust settings

```
sbx settings set kit.allowedSources '["docker.io/","github.com/myorg/"]'
sbx settings set kit.allowLocalKits false
sbx settings set kit.allowExtractedAgents false
sbx settings set kit.requireSignature true
sbx settings set kit.trustedSigners '[{"identityRegexp":"^.*@docker\\.com$","issuer":"https://accounts.google.com"}]'
sbx settings set kit.ignoreTransparencyLog true
```

- `kit.allowedSources` default `["docker.io/"]`. **Replaces the whole list.**
  Prefix matching is on path-segment boundaries: `github.com/myorg/` allows
  that organisation and not `github.com/myorg-other/`.
- `kit.trustedSigners` default is Docker employee identities. Setting it
  alone does not require signatures.
- `kit.requireSignature` rejects unsigned kits, non-matching signatures and
  **ZIP kits**.

This matters to this workshop directly: `factory/sbxenv.yaml` pulls the ACR
kit from a `git+https://github.com/shelajev/…` source, which is not in the
default `kit.allowedSources`. See `../factory/README.md`.

Checked against: DOCS (kits pages, SPEC-v2), RELEASE (v0.47.0, v0.48.0,
v0.49.1).

## 7. Settings

Precedence: environment variable, then `sbx settings set` override, then
the built-in default. Most changes take effect in about five seconds;
`RESTART` keys need `sbx daemon restart`.

| Key | Type | Default | Note |
| --- | --- | --- | --- |
| `browser.enabled` | bool | true | false blocks sandboxed processes from opening the host browser and prevents in-sandbox OAuth logins that redirect to localhost. |
| `clipboard.imagePaste` | bool | false | grants host clipboard **images**; text paste is unaffected. |
| `claude.remoteControl` | bool | false | lets Claude Code's `/remote-control` use its own session token. |
| `ssh.agentForwardingEnabled` | bool | true | false stops sandboxes asking the host agent to sign. |
| `ssh.agentSocketPath` | string | "" | fixed agent path; empty means each client's `SSH_AUTH_SOCK`. |
| `skills.defaultMode` | string | readonly | `readonly`, `readwrite`, `off`; applied only at creation. |
| `mcp.forceLocalGateway` | bool | false | needs a daemon restart. |
| `kit.allowedSources` | JSON | `["docker.io/"]` | see section 6. |
| `kit.allowLocalKits` | bool | true | covers local directories and v2 ZIP files. |
| `kit.allowExtractedAgents` | bool | true | exempts Docker's pinned agent references from the allowlist and the signature requirement. |
| `kit.requireSignature` | bool | false | rejects unsigned kits and ZIP kits. |
| `kit.trustedSigners` | JSON | Docker employees | a signature matching any entry is accepted. |
| `kit.ignoreTransparencyLog` | bool | false | for kits signed `--tlog-upload=false`; needs a TSA timestamp. |
| `daemon.log.maxSizeMB` | int | 50 | needs a daemon restart. |
| `daemon.log.maxBackups` | int | 3 | 0 means no count limit. |
| `daemon.log.maxAgeDays` | int | 0 | 0 means no age limit. |
| `sandbox.disk.dockerVolume` | string | 10g | minimum 512 MiB. |
| `proxy`, `proxy.sandbox`, `proxy.daemon` | string | "" | HTTP(S)/SOCKS5 URL, PAC source, `system` or `direct`. |
| `no_proxy`, `no_proxy.sandbox`, `no_proxy.daemon` | string | "" | a proxy exclusion does not grant network access. |
| `proxy.sshDirect` | bool | false | bypasses the upstream proxy for **cloud SSH only**. |
| `proxy.integratedAuth` | bool | false | NTLM/Kerberos on Windows. |
| `tls.allowNegativeSerial` | bool | false | relaxes TLS validation for negative-serial certificates only. |
| `diagnostics.autoUpload` | string | "" | `yes`, `no` or unset; does not grant consent for `sbx diagnose --upload`. |
| `diagnostics.autoUploadErrorCooldownInDays` | int | 1 | global across eligible errors. |

Also present in the v0.45.0-rc2 binary but absent from the docs reference
table: `model.providers`, `platform.allowExperimentalFeatures`,
`ssh.autoCreate`, `ssh.defaultAgent`, `ssh.defaultTemplate`,
`ssh.workspaceRoot`, plus `feature.udp-egress` and `feature.model`.

Checked against: DOCS (settings page), HELP.

## 8. Automation

- Authenticate without a browser: `echo "$DOCKER_PAT" | sbx login
  --username <id> --password-stdin`, with a PAT of at least Read scope.
- Create the workspace directory before `sbx create` / `sbx run`; without a
  terminal these fail when the directory is missing.
- `sbx secret import --all` imports every provider credential found in the
  CI environment into the global secret store.
- The documented unattended shape is create → exec → remove:
  `sbx create --name ci-task --clone claude .` then `sbx exec ci-task -- …`
  then `sbx rm --force ci-task`.
- `sbx prune --dry-run --format json` lists what a prune would remove.
- `sbx exec` starts a stopped sandbox first. It does not support detached
  mode.

Checked against: DOCS (automation page), HELP.

## 9. The eMudhra CA compromise

Eight eMudhra intermediate CA private keys were disclosed on 2026-10-20.
Docker rebuilt the sandbox template images for all built-in kits and
released v0.49.2. **Updating the CLI does not update existing sandboxes.**

Recreate with fresh images:

```
sbx stop <sandbox-name>
sbx rm <sandbox-name>
sbx create --name <sandbox-name> --pull always codex <workspace-path>
```

Save files and unpushed clone-mode commits first: removing a sandbox
deletes its filesystem.

Update the CA store in an existing sandbox instead:

```
sbx exec <sandbox-name> -- sudo apt-get update
sbx exec <sandbox-name> -- sudo apt-get install --only-upgrade -y ca-certificates
```

That path does not cover custom templates (rebuild those) or tools that
bundle their own CAs.

Checked against: RELEASE (v0.49.2), DOCS (security announcements).

## 10. Credentials and what crosses the boundary

- The agent runs as a non-root `agent` user (UID 1000) with passwordless
  sudo inside the VM. The hypervisor boundary is the control, not in-VM
  privilege separation.
- Each sandbox has its own Docker Engine. There is no path to the host
  daemon, except through a host-side stdio MCP server.
- API keys are injected into HTTP headers by the host proxy and never
  stored in the VM. OAuth responses are intercepted and the real token is
  replaced with a sentinel; `passthrough: true` opts out.
- The `PROXY` column of `sbx policy log` says what happened:
  `forward` (credential injected), `forward-bypass` (no injection),
  `transparent` (policy enforced, no injection), `network` (non-HTTP),
  `browser-open`.
- SSH agent forwarding is on by default. Private keys stay on the host, but
  a sandbox process can ask the forwarded agent to sign.
- Clipboard: sandbox processes can **write text** to the host clipboard but
  cannot read it. Reading images is a separate opt-in.
- The shared agent-skills store can be mounted `readwrite`, which puts
  participating sandboxes in one trust boundary. Use `--skills=off`.
- Model-provider credentials for `--model`/`--provider` go to the host's
  `llmman` service rather than being domain-injected. That service can
  forward the key to other endpoints it supports; selecting `--provider`
  does not restrict the key to that provider's endpoint.

Checked against: DOCS (security pages).

## 11. Git workflows

| | Direct | Clone (`--clone`) | Host worktree |
| --- | --- | --- | --- |
| Branch management | You, on the host | The agent, in the clone | You, on the host |
| Changes visible on the host | Immediately | After a fetch or a push | Immediately |
| The agent can use Git | Yes | Yes | **No** |
| Parallelism | No | Several agents in one sandbox | One sandbox per task |
| Fixed at creation | No | **Yes** | — |

- The clone-mode source is mounted read-only at `/run/sandbox/source` and
  covers the whole working directory including untracked and ignored files,
  so `.env` is still readable. Clone mode protects against modification, not
  inspection.
- The `sandbox-<name>` remote is served by a git-daemon inside the sandbox,
  bound to localhost on the host. `sbx stop` stops it; a restart assigns a
  new ephemeral port and the CLI rewrites the remote URL; `sbx rm` removes
  the remote entry.
- Local-path remotes (`file://`, filesystem paths) are **not** copied into
  the clone.
- Clone mode is rejected from inside a Git worktree other than the main
  one.
- Direct-mode hard links reach outside the workspace, and filesystem access
  policies do not block that.
- A host worktree mount gives the sandbox no Git access at all: it cannot
  resolve the `.git` pointer file.

Checked against: DOCS (git workflow page).

## 12. The shared skills store

- `sbx skills add <repository>` installs skills from a Git repository; a
  GitHub `owner/repository` shorthand works.
- `sbx skills import` copies skills from `~/.agents/skills`,
  `~/.claude/skills`, `~/.config/opencode/skills`, `~/.copilot/skills`,
  `~/.cursor/skills`, `~/.factory/skills`, in that order. The first copy
  wins; the others are skipped with a warning.
- `sbx skills import --dry-run` previews without copying.
- The store lives at
  `~/Library/Application Support/com.docker.sandboxes/sandboxes/agent-skills`
  on macOS.
- `sbx skills update` refreshes only skills installed with `sbx skills add`.
  A skill imported from the host is refreshed by importing again.
- The store is mounted read-only by default. `--skills=off|readonly|readwrite`
  chooses the mode at creation; the mode does not change afterwards.
- `sbx reset` clears the store.

Checked against: DOCS (agent-skills page), HELP.

## 13. Runtime mounts

`sbx mount SANDBOX HOST[:CTR_TARGET[:ro|rw]]` bind-mounts a host path into
a running sandbox. `HOST` alone mounts at the same absolute path inside the
container, the same convention the create-time workspace mount uses.
`HOST:CTR_TARGET` is a real bind mount (setns + open_tree + move_mount), not
a symlink. Mounts are idempotent and are restored after stop and start. The
mount policy is checked before the call reaches the daemon, using the
effective read-only intent.

`sbx umount SANDBOX HOST[:CTR_TARGET]` revokes. The allowlist is the source
of truth, so a host path deleted after mounting can still be revoked. For a
stopped sandbox, `umount` removes the saved mount so it is not restored.

Filesystem policies are enforced at workspace mount time — that is, at
sandbox creation. A runtime `sbx mount` is checked against the mount policy
before it reaches the daemon.

Checked against: HELP, DOCS.

## 14. SSH and editor integrations

- `sbx setup ssh` writes a managed block to `~/.ssh/config` with
  `Host *.sbx` and a `ProxyCommand` that relays to the daemon over its
  Unix socket. `IdentityAgent none`, `IdentityFile /dev/null`: no key is
  used and no SSH server runs inside the sandbox. The daemon accepts a
  connection only while you hold an active Docker login.
- The sandbox name is the hostname: `ssh <name>.sbx`. Named app instances
  use `ssh <name>.sbx-<app>`.
- `sbx setup ssh --auto-create` (v0.48.0+) registers connections in Codex
  and Claude Desktop on macOS and Windows. Each creates a dedicated
  sandbox on first use, with **no host workspace mounted** — clone or copy
  the project in. Remove them with `sbx setup ssh remove`.
- SSH sessions start in the sandbox workspace, matching `sbx exec`.
- SSH clients can forward a port to a loopback address inside the sandbox.
- Editor integrations: VS Code, Cursor, Claude Desktop, ChatGPT, T3 Code,
  Orca.

Checked against: DOCS (integrations page), HELP.

## 15. Copying files

`sbx cp SRC DST` where one side is `SANDBOX:PATH`. Copying between two
sandboxes is not supported. A directory is placed at the destination; if
the destination exists as a directory the source goes inside it.

Security: v0.48.0 blocks local paths that escape a sandbox workspace
through symbolic links. v0.49.1 reports a missing destination directory
without extra tar errors.

Checked against: HELP, RELEASE (v0.48.0, v0.49.1).

## 16. Diagnosing

`sbx diagnose` checks for a missing CLI binary, daemon reachability, a
CLI/daemon version mismatch, missing storage directories, broken
authentication, `mkfs.ext4`, running CLI and shim processes, an
uninitialised global network policy, and a ZFS setting that can slow image
builds. `--format table|json|yaml|github-issue` (v0.48.0+; `--output`/`-o`
before that). Without a terminal, `--upload` saves the bundle, skips the
upload and exits 0 — a successful exit does not mean an upload happened.

`sbx diagnose --upload` prints a diagnostics ID. Bugs go to
`github.com/docker/sbx-releases/issues`.

Checked against: RELEASE (v0.48.0, v0.49.1), DOCS (troubleshooting).

## 17. Known problems still open

| Problem | Status |
| --- | --- |
| UDP to a denied hostname permitted via an allow rule for the resolved IP, with multiple tracked hostnames | Open (v0.46.0 fixed the TCP case only) |
| `**.example.com`, port ranges, port wildcards and CIDRs in v2 kit network rules | Parsed, enforcement pending |
| The proxy cannot mask a successful Anthropic API-key creation response | Fixed in v0.47.0 by returning an error instead of forwarding it |
| A kit signature does not cover the bytes its install command downloads | By design; pin those by digest |
| An agent can spend an injected credential through any allowed host | By design |
| `sbx policy log` does not evaluate HTTP method and path | By design |
