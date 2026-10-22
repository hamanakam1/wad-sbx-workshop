# The Docker Sandbox field guide

This file is generated. The source of truth is the dashboard's static asset
`static/tabs/sbx-guide.html.txt`, which is served same-origin and injected by
`static/tabs/sbx-guide.js`; the dashboard's own test suite is the contract for
that HTML.

Rebuild it with:

    python3 scripts/build-field-guide.py

and check it is current with:

    python3 scripts/build-field-guide.py --check

The confidence markers travel with the claims. `[verified]` means a primary
source said so, `[inferred]` means the source supports it but not directly, and
`[corrected]` means an earlier version of this guide was wrong and the text
has been fixed.

---

- [The one idea](#sbxg-idea)
- [What crosses](#sbxg-boundary)
- [On this Mac](#sbxg-stack)
- [Three layers](#sbxg-layers)
- [Anatomy of a kit](#sbxg-kit)
- [The workshop's kits](#sbxg-audit)
- [What it doesn't stop](#sbxg-limits)
- [The isolation ladder](#sbxg-ladder)
- [Concepts that transfer](#sbxg-concepts)
- [What changed](#sbxg-whatsnew)
- [Automation](#sbxg-automation)
- [Connectors](#sbxg-connectors)
- [Supply chain](#sbxg-supply)
- [Skills, mounts, SSH](#sbxg-surface)
- [Troubleshooting](#sbxg-trouble)
- [Hands-on path](#sbxg-practice)
- [Your setup](#sbxg-yours)
- [Sources](#sbxg-sources)

### The one idea

A sandbox is a promise about what crosses a boundary. Stop asking "what may the agent do inside?" and ask "what can it reach from inside?"

Every Docker Sandbox is a small Linux virtual machine with its own kernel, started for one agent. Inside it the agent is deliberately powerful: it has `sudo`, its own Docker Engine, and full control. That is fine, because the protection is the hypervisor wall around the VM, not permissions inside it. Docker's docs put it bluntly: *"The hypervisor boundary is the isolation control, not in-VM privilege separation."* [verified] [security/isolation](https://docs.docker.com/ai/sandboxes/security/isolation/)

So understanding sandboxes means understanding the handful of deliberate holes in that wall. There are six. Learn them and you can reason about any sandbox product, not just this one.

Keep one more distinction in view from the start. **Isolation** is *where* code runs and what it can physically reach. **Authorization** is *what it is allowed to do* with what it can reach. A perfectly isolated agent holding a GitHub token with write access can still push bad code. Sandboxes are mostly about the first; you still need the second.

### What crosses the boundary

This is the workshop's setup, drawn from the verified facts: the host on the outside, the microVM in red, and every arrow that crosses between them.

Made with Archify. [Open the interactive version](/api/archify/artifacts/0ff57594686e4f2fbbe7fc3150fad7d0), with guided views for files, network and host tools; it is also listed on the Archify tab.

| Crossing | How it works | What protects you | What it does not stop |
| --- | --- | --- | --- |
| Your project files | `sbx run` mounts the folder read-write over virtio-fs, at the same absolute path, so edits are live on your Mac. | Only the mounted path is visible. `--clone` gives the agent a private copy; `path:ro` mounts read-only. | In direct mode it can rewrite `.git/hooks` (invisible to `git diff`), CI files, `package.json` scripts, `.claude/settings.json`. Hard links reach outside. |
| Network | All outbound TCP goes through a proxy on the host. You choose a preset on first run: Open, Balanced or Locked Down. UDP is off, ICMP blocked. | Deny by default (Balanced and Locked Down). Rules per host, wildcard, port; deny beats allow. | Every allowed host is an exit route. Balanced includes broad wildcards such as `*.googleapis.com`. |
| Credentials | `sbx secret set` stores the key in the macOS Keychain. The agent sees a placeholder like `proxy-managed`; the proxy writes the real header for that service's domains. | The agent cannot read or copy your key. | It can still *use* the key against those domains. Values passed with `-e`, and kits with OAuth `passthrough: true`, do enter the VM. |
| Host tools (MCP) | The agent gets one MCP gateway endpoint. Servers you register with `sbx mcp add` run **on your Mac**. | The agent sees named tools, not your filesystem. | A stdio server runs with your user's full permissions, so its tool list *is* the scope. In dynamic mode the agent can attach any registered server. |
| Published ports | `sbx ports NAME --publish 3102:8080`, or `ports:` in sbxenv.yaml. The server inside must listen on all interfaces. | Binds to 127.0.0.1 by default (IPv4 since 0.42). | A non-loopback `hostIP` would expose it to your network. [inferred] |
| SSH agent | Forwarding is on by default. | Keys stay in your host agent. | Processes inside can ask it to sign. Turn it off with `sbx settings set ssh.agentForwardingEnabled false`. |

Sources: [isolation](https://docs.docker.com/ai/sandboxes/security/isolation/) · [credentials](https://docs.docker.com/ai/sandboxes/configuration/credentials/) · [mcp-gateway](https://docs.docker.com/ai/sandboxes/mcp-gateway/) · [usage](https://docs.docker.com/ai/sandboxes/usage/) · `sbx ports --help`

### What's actually running on this Mac

Docker's docs promise "a lightweight microVM with its own Linux kernel" but never name the macOS machinery underneath. A researcher read the installed `Sbx.app` bundle (entitlements, linked libraries, strings) and a fact-checker corrected the first guess. This is the stack as far as the bundle shows it.

- Inside the VM **The agent's container** plus a **private Docker Engine**. `docker compose up` talks to this engine, never to a host daemon. That is why the workshop needs no Docker Desktop. [verified]
- Guest kernel **Linux 7.0.12, arm64**, shipped in the bundle as `nerdbox-kernel-arm64` with an EROFS root filesystem. [inferred]
- hypervisor boundary
- Runtime shim **containerd `nerdbox` shim**, which holds the `com.apple.security.hypervisor` entitlement and runs container processes inside a VM. [inferred]
- VM monitor **Docker's "Sailor" VMM** (`libsailor.dylib`), linking Apple's framework directly. [corrected] The first guess was libkrun, which the upstream nerdbox README lists; this build does not use it.
- macOS **Hypervisor.framework**, Apple's low-level API: VMs as processes, virtual CPUs as threads. Not the higher-level Virtualization.framework. [inferred]
- Hardware **Apple silicon** virtualization extensions. On Linux the same design needs KVM; on Windows, the Windows Hypervisor Platform. [verified]

You can check the evidence yourself; these commands only read:

**Host · read-only**

```
codesign -d --entitlements - ~/Applications/Sbx.app/Contents/Helpers/containerd-shim-nerdbox-v1
otool -L ~/Applications/Sbx.app/Contents/Helpers/lib/libsailor.dylib
sbx version --json
sbx daemon status
```

Two practical consequences. Each sandbox is a whole VM with its own daemon and image cache, so nothing is shared between sandboxes (a 20 GB root disk by default). And the VM boundary itself can have bugs: release 0.42.0 fixed a virtio-fs symlink hole that let a guest read or modify host files outside the workspace (CVE-2026-77179). [verified]

**Versions, and the one that matters today.** The workshop pins `v0.45.0-rc2`. That is a release candidate and it is four minor releases behind: **v0.46.0**, **v0.47.0**, **v0.48.0** and **v0.49.2** have all shipped since, and every one of them carries proxy or filesystem security fixes. The strip at the top of this view shows the version installed right now, and [What changed](#sbxg-whatsnew) lists what you are missing.

**The v0.49.2 advisory is the urgent one.** On 20 October 2026 eight eMudhra intermediate CA private keys were disclosed. Docker rebuilt the built-in sandbox template images and shipped v0.49.2. Updating the CLI does *not* update sandboxes you already have: their certificate store still trusts the compromised authorities. Recreate them, or upgrade the store inside them, exactly as [What changed](#sbxg-whatsnew) shows. [verified]

Stay on the pinned build for the workshop chapters so the commands you learn match the ones you will read; upgrade before pointing agents at repositories you care about.

### Three layers of configuration

Everything you configure lands in one of three places. Most confusion clears up once you know which layer a setting belongs to.

| Layer | What it is | Use it when | Watch out for |
| --- | --- | --- | --- |
| Template | A saved filesystem image of a sandbox (`sbx template save`, then `sbx run -t`). Files only, no runtime policy. | You set something up by hand and want to start from it again. | It captures anything left on disk, secrets included. |
| Kit | A YAML spec plus files: what to install, which hosts may be reached, which credentials the proxy injects, which env vars to set. A `sandbox` kit is a whole agent; a `mixin` adds to one. | You want reusable, reviewable tooling plus the policy it needs. | Install steps run as **root** by default. A kit is code you execute, and instructions your agent reads. |
| Environment file | `sbxenv.yaml`: one concrete sandbox (agent, workspace, kits and their args, CPUs, ports, secrets, MCP servers, host lifecycle commands), applied through a plan you approve. | You want one project's sandbox to be repeatable. The workshop's `factory/sbxenv.yaml` grows chapter by chapter. | Lifecycle commands run on the host with your privileges. The workshop launcher uses `--auto-approve`, which skips the plan review. |

Rule of thumb: a **template** snapshots state, a **kit** packages capability and policy, an **environment file** wires one project together. Changes to kits, ports and secrets take effect only when the sandbox is next created. [environment-files](https://docs.docker.com/ai/sandboxes/configuration/environment-files/)

**Version trap.** Docker's docs landing page describes *v3* kits (`kind: workload`, built as OCI images). The rc2 validator accepts only schemaVersion 1 and 2, every workshop kit is v2, and v3 kits cannot be combined with v2 ones (including the built-in agents). Use the page titled "Kits v2" as your reference while on the workshop. [verified]

### Anatomy of a kit

This is the small mixin you write in exercise 4. It validated on the rc2 binary. Each numbered comment maps to a note.

**factory/my-probe/spec.yaml**

```
schemaVersion: "2"            # 1
kind: mixin                   # 2
name: my-probe
description: "Learning mixin: one env var, one allowed host, one helper."
args:                         # 3
  probe_host:
    default: example.com
    pattern: '^[a-z0-9.-]+$'
permissions:                  # 4
  network:
    allow: ["${{ kit.args.probe_host }}"]
environment:                  # 5
  variables:
    PROBE_HOST: "${{ kit.args.probe_host }}"
    PROBE_NOTE: "set by the my-probe kit, not a secret"
setup:
  install:                    # 6
    - user: agent
      description: Install a tiny probe command
      command: |
        set -eu
        mkdir -p "$HOME/.local/bin"
        cat > "$HOME/.local/bin/probe" <<'SH'
        #!/bin/sh
        host="${1:-$PROBE_HOST}"
        if curl -fsS -o /dev/null --max-time 8 "https://$host/"; then echo "ALLOWED  $host"; else echo "BLOCKED  $host"; fi
        SH
        chmod +x "$HOME/.local/bin/probe"
  files:                      # 7
    - path: /home/agent/.my-probe-installed
      content: "workspace=${WORKDIR}\n"
      mode: "0644"
```

1. **schemaVersion "2"** selects a strict grammar: any unknown field is an error, so a typo like `netwrok` fails validation instead of silently doing nothing.
1. **kind**: `mixin` adds to an existing agent and may not declare `sandbox:`. A `sandbox` kit defines the whole agent image.
1. **args** are string inputs, spliced into the YAML as raw text *before* it is parsed. The `pattern` is what stops a value from breaking out of a shell string. Never pass secrets as args.
1. **permissions.network.allow** adds hosts for this sandbox only. Lists add up across kits and deny always wins. In v2 a grant lasts the sandbox's whole life [inferred], so a host you needed only for installing stays open to the agent. Under organization governance, kit allow rules are inactive.
1. **environment.variables**: plain, visible env vars. If two kits set the same one, the last kit silently wins.
1. **setup.install** runs through `sh -c`, **as root unless you say `user: agent`**, at creation and again on every recreate. So it must be idempotent, and `set -eu` makes a failed step fail loudly.
1. **files** are written at start as the agent user; only `${WORKDIR}` is substituted.

`sbx kit validate` checks structure only. It does not run install commands, test that hosts are reachable, or catch conflicts between kits. A researcher confirmed that on rc2 a `**.example.com` rule validates cleanly while the docs call multi-label wildcards "enforcement pending". Validation is not enforcement. [verified] [Kits v2](https://docs.docker.com/ai/sandboxes/customize/kits/) · [SPEC-v2](https://github.com/docker/sbx-kits-contrib/blob/main/spec/SPEC-v2.md)

### The workshop's kits, audited

Reading real kits is the fastest way to learn what good looks like. All five validated; they differ in how carefully they handle the supply chain.

| Kit | What it adds | Download check | Arg safety | Runs as | Worth noticing |
| --- | --- | --- | --- | --- | --- |
| hello-kit | A one-line command | nothing downloaded | no args | agent | No `set -e`: a failed `mkdir` would be masked by the last command's exit code. |
| pi | The Pi assistant, pinned 0.85.1 | checks SHA256SUMS fetched from the same release | no pattern | agent | Idempotent (`command -v pi` guard). Allows `api.anthropic.com` and `generativelanguage.googleapis.com` for the sandbox's life. |
| herdr | Session manager, pinned 0.9.0 | none | no pattern | agent | Re-downloads on every recreate. [corrected] Upstream *does* publish a sha256 digest per binary on the release page; the kit could pin it and doesn't. |
| multi-provider | A whole sandbox kit: Claude, Codex, Gemini credentials | mutable image tag; Codex via npm, unpinned hashes | semver pattern | root, then chown | Always writes settings that turn off agent approval prompts (`bypassPermissions`, `danger-full-access`), so the sandbox is the only boundary. [corrected] |
| ACR @ f446b95 | Coding policy and review skill | checksums.txt from the same release | pattern + single quotes | root (to `/usr/local/bin`) | Pinned to a 40-hex commit. Its agent instructions call running `acr install` "mandatory": a kit also supplies *instructions*. |

#### Five mistakes these kits teach

1. **Self-verified downloads.** A checksum file fetched from the same release proves the file wasn't corrupted, not that the release wasn't replaced. The strong fix: put the expected per-architecture hash inside the commit-pinned kit. A commit pin or signature covers the kit's text, never the bytes it downloads.
1. **Unconstrained args in shell strings.** A value is spliced in literally. Inside double quotes, `;` and `#` are harmless text, but `"`, `$(…)` and backticks break out. [corrected] ACR's fix (pattern + single quotes) is the model.
1. **Wrong user.** Install defaults to root. A root step that writes under `/home/agent` must `chown` exactly what it touched.
1. **Not idempotent, or failing silently.** Install re-runs on every recreate; "Install commands completed" only means exit status 0.
1. **Allow lists that drift.** Every credential inject domain must also be allowed; fields like `skipIfEnv` do nothing in v2; a host allowed for install stays allowed for the agent.

### What it does not protect against

A sandbox bounds the damage; it doesn't make an agent safe. Docker's own advice for the direct mount: *"Treat sandbox-modified workspace files the same way you would treat a pull request from an untrusted contributor."*

- Changes to a directly mounted repo, including hooks, CI and build scripts that run later on your Mac. [verified]
- Data leaving through any allowed host. [verified]
- The agent *using* injected credentials, and asking your forwarded SSH agent to sign. [inferred] from how injection works
- Host-side MCP servers and environment-file lifecycle commands, which run with your full permissions. [verified]
- Hard links in the workspace that point outside it; clipboard writes to your Mac; a shared read-write skills store linking sandboxes. [verified]
- Bugs in the boundary itself (0.42.0 fixed two guest-to-host symlink escapes). [inferred] as a residual risk from the release notes
- Prompt injection. The docs don't discuss it. Isolation limits what a manipulated agent can reach; it doesn't stop the manipulation. [inferred]

### Where this sits: the isolation ladder

"Sandbox" is not one technology. Every rung below answers the same three questions: what does the code share with you, what stops it, and what does that cost? Higher rungs share less. The highlighted rungs are where today's agent tools live.

#### Processes and user permissions

The kernel checks file ownership and permission bits. It isolates nothing between processes of the same user: an agent running as you can read `~/.ssh` and `~/.aws`.

An unsandboxed coding agent lives here.

#### chroot

Changes where `/` resolves. The man page: "not intended to be used for any kind of security purpose". Root escapes trivially.

#### Containers: namespaces + cgroups

Namespaces give a process its own view of PIDs, mounts, network and hostname; cgroups cap resources; OCI standardises the format. All containers share the host kernel, so one kernel bug is a host compromise, and mounting `docker.sock` hands over the host daemon.

Dev Containers (a reproducibility spec with no security claims).

#### User-space kernel: gVisor

An "application kernel" in Go answers most system calls itself, shrinking the host kernel's attack surface at the cost of compatibility and per-call overhead.

Modal's sandboxes.

#### Full virtual machines

Hardware virtualization runs a guest with its own kernel on virtual devices (KVM on Linux; Hypervisor.framework or Virtualization.framework on macOS). Only the devices you expose are shared.

#### MicroVMs

The same idea with a deliberately tiny device model. The Firecracker paper (NSDI 2020) reported under 5 MB overhead and boot to application code in under 125 ms, as of that paper.

Docker Sandboxes · E2B (Firecracker).

#### OS policy sandboxes

Same machine, same kernel, but the kernel enforces a per-process policy: macOS Seatbelt (`sandbox-exec`, marked deprecated yet still used), Linux Landlock, seccomp, bubblewrap. Cheap, but only as good as the policy you write; the kernel docs say seccomp alone "isn't a sandbox".

Claude Code's sandboxed Bash · Codex CLI (Seatbelt on macOS; bubblewrap + seccomp on Linux).

#### Language sandboxes: WebAssembly / WASI

The code has no system calls at all; it can only call functions the host hands it. The cleanest capability model, but coding agents need a real shell and compilers.

Notice the trade: policy sandboxes are nearly free but share your kernel; microVMs cost a VM each but put a hypervisor between the agent and everything else. Anthropic's own docs point to Docker Sandboxes as the microVM option when you want stronger separation than Claude Code's built-in sandbox. [sandbox-environments](https://code.claude.com/docs/en/sandbox-environments) · [Firecracker paper](https://www.usenix.org/system/files/nsdi20-paper-agache.pdf) · [gVisor](https://gvisor.dev/docs/) · [namespaces(7)](https://man7.org/linux/man-pages/man7/namespaces.7.html)

### Concepts that transfer

These six ideas are the actual field. Every sandbox product is a particular combination of them.

#### Isolation vs authorization

Isolation limits what code can reach; authorization limits what it may do with it. Claude Code's docs draw exactly this line: permission rules gate tools; the sandbox restricts shell subprocesses.

Here: MasterBoard's `no-auto` fence is pure authorization. sbx would add isolation underneath it.

#### Least privilege and blast radius

Give each program only what it needs, so a failure or hijack is bounded by those privileges (Saltzer & Schroeder, 1975).

In sbx: clone mode instead of direct mount, Locked Down plus explicit allows, one sandbox per role.

#### Egress allowlisting

Deny outbound by default, then allow named destinations. It blocks an injected `curl evil.com`, but the list is only as good as its broadest entry, and hostname checks without TLS inspection can be fooled by domain fronting.

Default-deny is not universal: sbx's Open preset, E2B and Modal allow outbound by default.

#### Secret injection vs exposure

Exposure: the key sits in an env var the agent can print or send anywhere. Injection: the agent holds a placeholder and a proxy outside swaps in the real value for approved hosts only.

It stops theft, not use: a hijacked agent can still spend the key through allowed hosts while the session lives.

#### Supply-chain pinning

Refer to kits and images by immutable commit or digest and verify signatures, so what runs is what you reviewed.

A pin covers the kit itself, not what its install step downloads. That's why the embedded hash matters.

#### Why agents are different

Classic sandboxing isolates untrusted *code*. An agent is trusted code steered by untrusted *data*: web pages, READMEs, issues, tool output. Prompt injection lets that data use every permission the agent legitimately holds.

Hence the pairing: isolation, default-deny egress, injected secrets, and a human checkpoint for decisions, as the workshop's chapter 6 does.

Sources: [Claude Code sandboxing](https://code.claude.com/docs/en/sandboxing) · [Codex sandboxing](https://learn.chatgpt.com/docs/sandboxing) · [OWASP LLM01](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) · [Saltzer & Schroeder](https://www.cs.virginia.edu/~evans/cs551/saltzer/)

### What changed

The guide below was written against `v0.45.0-rc2`. This is what has landed since, and what it changes for you. Every row was read out of the release notes or out of the binary's own help text, not from memory.

Fixed raw TCP to a *denied* hostname being permitted because an allow rule existed for the address that hostname resolved to. The UDP case with several tracked hostnames is still open. [verified]

`sbx run --rm` removes the sandbox when the agent session ends. Kits can declare `com.docker.sandbox/long-running@1` to keep a local sandbox alive after every session disconnects. `sbx kit sign` and `sbx kit push --sign` now work on registries that refuse manifest deletion, GitHub Container Registry and Docker Hub among them. The proxy stopped letting a capitalised or trailing-dot provider hostname receive a real OAuth token instead of the placeholder.

The release that breaks your scripts. Flags moved and the JSON documents changed shape: [Automation](#sbxg-automation) has both tables. It also added `sbx setup ssh --auto-create` for Codex and Claude Desktop, the v3 kit capabilities `ssh-agent@1` and `git-identity@1`, `sbx kit add` for kits with startup commands, the `SBX_AGENT` environment variable, and `sbx mcp unload`.

`sbx kit validate --offline` checks a local kit without starting the daemon or signing in. `sbx policy refresh` applies a governance change immediately instead of waiting for the polling interval, though it only affects new connections. `browser.enabled`, `daemon.log.maxSizeMB` and its two siblings became settings. An explicit CIDR deny now beats a matching domain allow. The full templates gained a C/C++ toolchain.

The security response, described next.

#### The eMudhra certificate advisory

On 20 October 2026 eight eMudhra intermediate certificate authority private keys were disclosed. Docker rebuilt the sandbox template images for all built-in kits and released v0.49.2 with updated Go and a rebuilt `llmman`. **Updating the CLI alone does not update sandboxes you already have.** Their trust store still contains the compromised authorities. [verified]

Two ways out. Recreate the sandbox with a refreshed image, which is the clean answer:

**Host**

```
sbx stop learn-01
sbx rm learn-01
sbx create --name learn-01 --pull always shell "$(pwd)/sample-app"
```

Or update the certificate store in place, which keeps the sandbox and everything in it. `sbx exec` can run the command as root in the guest, which is why the two lines name a user:

**Host**

```
sbx exec -u root learn-01 -- apt-get update
sbx exec -u root learn-01 -- apt-get install --only-upgrade -y ca-certificates
```

The second path does not cover custom templates, which have to be rebuilt, or tools that bundle their own certificate authorities. Save files and unpushed clone-mode commits before removing anything: removing a sandbox deletes its filesystem. [security announcements](https://docs.docker.com/security/security-announcements/)

#### What v0.48.0 changed in the command line

| Command | Before 0.48 | From 0.48 |
| --- | --- | --- |
| `sbx run`, `sbx env run` | `--detached` | `--detach` or `-d` |
| `sbx logout` | `--yes`, `-y` | `--force` or `-f` |
| `sbx env plan`, `create`, `run` | `--auto-approve`, `-y` | `--force` or `-f` |
| `sbx create` | `--quiet`, `-q` | `--no-progress` |
| `sbx template save` | `-d` for a description | `--description` |
| `sbx diagnose` | `--output`, `-o` | `--format table|json|yaml|github-issue` |
| `sbx secret ls`, `rm` | `--env` | `--env-name` |
| `sbx policy log` | `--quiet`, `-q` | removed, nothing replaces it |
| `sbx mcp add` | `--skip_auth` | `--skip-auth`, old spelling still accepted and warns |

Two more rules came with the same release, and they catch people out more than the renames do. First, `--format`, `--json` and `-q` are mutually exclusive on the commands that share them, so even `--json --format json` is refused. Second, **results go to stdout and everything else goes to stderr**: errors, warnings, corrective hints, daemon-start progress, image pulls and kit-build progress. A script that captured only stdout will report a clean run while the reason for the failure is on the other stream.

Confirmation also changed. A prompt needs a terminal, so `echo y | sbx secret set …` no longer confirms anything; a command that needs confirmation exits with status 1 when it has no terminal, and declining returns non-zero too. `sbx create` and `sbx run` now fail when the workspace directory does not exist, and print a `mkdir -p` hint instead of creating it. [verified]

#### What v0.48.0 changed in the output

The `--json` documents changed shape. A parser written against the old one does not fail loudly: it finds an empty list and concludes there are no sandboxes.

| Command | Before 0.48 | From 0.48 |
| --- | --- | --- |
| `sbx ls` | `{"sandboxes": [...]}` | a bare array; rows carry `created_at`, `last_used_at`, `detached` |
| `sbx template ls` | `{"images": [...]}` | a bare array |
| `sbx mcp ls` | `{"gateway": {...}, "servers": [...]}` | a bare array; each row carries `gateway` |
| `sbx policy log` | `allowed_hosts`, `blocked_hosts` | `{"updated_at", "allowed": [...], "blocked": [...]}` |
| `sbx ports` | `port` on cloud rows | cloud: `sandbox_port` and `url`; local: `host_ip`, `host_port`, `sandbox_port`, `protocol` |
| `sbx rm`, `stop`, `template rm` | one line per target | an array of `{action, resource, name, id?, error?}` |

The one-line change that catches the most scripts is the first: `.sandboxes[].name` becomes `.[].name`. [Automation](#sbxg-automation) shows a normaliser that reads both.

Sources: [sbx release notes](https://github.com/docker/sbx-releases/releases) · [run sandboxes in CI](https://docs.docker.com/ai/sandboxes/workflows/automation/)

### Automation

Everything above was written for a person at a terminal. This is the shape for work nobody is watching: a script that creates a sandbox, runs something inside it, and cleans up, leaving a document that says what happened.

#### The shape

**Host**

```
sbx create --name learn-auto --clone shell "$(pwd)/sample-app"
sbx exec learn-auto -- npm test
sbx rm learn-auto
```

Three details carry the weight. `sbx create` makes the sandbox without an agent session, so nothing waits for a prompt. `sbx exec` runs a command inside it and starts it first if it was stopped; it has no detached mode, and its exit status is the command's exit status. The last line removes the sandbox. A script needs `--force` on that last command, because a confirmation prompt has no terminal to answer it and would otherwise hang the run. [verified]

#### Signing in without a browser

An unattended run has no terminal to sign in with. Use a Docker personal access token with at least Read scope:

**Host**

```
echo "$DOCKER_PAT" | sbx login --username "$DOCKER_ID" --password-stdin
sbx secret import --all
```

`sbx secret import --all` copies the provider credentials it finds in the environment into the global secret store, so the sandbox can use them without them ever entering the virtual machine. Add `--force` to overwrite an entry that is already there.

#### Rules a script has to respect

- **Create the workspace directory first.** Without a terminal, `sbx create` and `sbx run` fail when the path does not exist.
- **Never parse the human-readable output.** It changes between releases. Use `--format json`, and handle both list shapes.
- **Read both streams.** Results are on stdout; the reason for a failure is on stderr.
- **Treat a non-zero exit as incomplete.** A command that needed a confirmation it could not get exits 1 without having done anything.
- **Check what exists before removing it.** `sbx mcp rm` fails for a server that was never registered, and `sbx secret rm` fails when nothing matches the selector.

#### A normaliser that survives both shapes

This is the whole compatibility problem in a few lines. It accepts the wrapped list and the bare list, so it works before and after v0.48.0 without knowing which it got.

**Python**

```
def sandbox_list(document):
    if isinstance(document, list):
        rows = document
    elif isinstance(document, dict):
        rows = document.get("sandboxes")
    else:
        rows = None
    if not isinstance(rows, list):
        raise ValueError("no sandbox list in the sbx output")
    return rows
```

Ask for the version first and you can also pick the right flag spelling. Compare the numbers, not the strings: `"v0.49.2"` sorts before `"v0.9.0"` as text, and after it as a version.

**Python**

```
import json, shlex, subprocess

def sbx(args):
    return subprocess.run(shlex.split(args), capture_output=True, text=True)

def as_tuple(text):
    parts = text.lstrip("v").split(".")
    return tuple(int(part) for part in parts)

def sbx_version():
    document = json.loads(sbx("sbx version --json").stdout)
    return as_tuple(document.get("client").get("version"))

def diagnose():
    flag = "--output" if sbx_version() 
<
 (0, 48, 0) else "--format"
    return sbx("sbx diagnose " + flag + " json")
```

**Do not compare versions as strings.** `"v0.49.2" < "v0.9.0"` is true as a string comparison and false as a version comparison. Parse the three numbers and compare the tuples, as above.

#### Evidence, not just an exit code

An exit code tells you the command failed. It does not tell you which build of `sbx` ran, what the tree looked like, or how long it took. The connector in this workshop's fork writes that down: the runner in `connectors/sbx-runner/` creates a disposable sandbox, runs the command, and writes a receipt named `sbx-sandbox-test-receipt-v1` that records the client and server versions, a digest of the tree it ran against, the command, the exit status, the timings and the output tails. It removes the sandbox afterwards, so the receipt is the only thing that survives.

**Host**

```
python3 connectors/sbx-runner/sbx_runner.py run npm test --workspace . --receipt .local/receipts/run.json
```

Sources: [run sandboxes in CI](https://docs.docker.com/ai/sandboxes/workflows/automation/) · [release notes](https://github.com/docker/sbx-releases/releases)

### Connectors

A sandbox has no host filesystem and no route to your host's binaries. So when an agent inside one needs to reach something on your Mac, you do not mount it. You give it a *named tool*: a host program that speaks MCP, reachable through the gateway, whose tool list *is* the scope of what the agent can do.

#### Static mode

`sbx run claude . --static-mcp beans,linear` gives the sandbox exactly those servers, chosen once at creation and fixed afterwards. The discovery tools are not exposed at all.

Prefer this. The agent cannot attach anything you did not name.

#### Dynamic mode

Leave `--static-mcp` off and the gateway offers `mcp-find`, `mcp-add` and `mcp-config-set`. The agent can attach any server registered on this Mac, including one registered a minute ago.

Convenient, and exactly as broad as your registration list.

#### The full registration surface

| Flag | What it does |
| --- | --- |
| `--url` | A remote MCP endpoint, a community-registry URL, a `server.json` manifest URL, or a `dhi.io/image:tag` or `dhi.io/image@sha256:…` reference. Other image forms are refused. |
| `--local` | Resolve an OCI stdio package and run it on the host with `docker run`. Docker must be installed and running. |
| `--command`, `--args`, `--dir` | An explicit host stdio server: the executable, its arguments, its working directory. This is the form a local script uses. |
| `--header` | A custom HTTP header for a remote endpoint, in curl form. Repeatable. A `${placeholder}` is filled from a stored secret. Transport-owned headers are refused. |
| `--scope`, `--no-scope` | The OAuth scopes to ask for at consent time, or the instruction to ask for none. |
| `--client-id`, `--oauth-authorization-server`, `--resource` | A pre-registered OAuth client, a custom authorisation-server metadata URL, and an RFC 8707 resource indicator. |
| `--skip-auth` | Register an OAuth server without starting the sign-in flow now. |
| `--skip-ssrf-check` | Suppress the warning that fires when a URL resolves to a private or loopback address. The connection is still made: the check makes the address visible, it does not block it. |
| `--disable-http2` | Do not negotiate HTTP/2. The fix for MCP streams that stall behind a proxy. |

There is no `--client-secret` flag. A confidential client keeps its secret in the host credential store, bound to an OAuth identity, and you remove it with `sbx secret rm`.

#### Authorisation, cataloguing and attaching

- `sbx mcp catalog` lists the servers in Docker's hosted catalog. It needs no sandbox, no daemon and no sign-in, and it is what cloud sandboxes resolve their static MCP set against.
- `sbx mcp auth <name>` runs the OAuth flow. It asks for `offline_access` when the server advertises it and the scope set came from the recorded defaults or from the resource's requirements, which is what lets the server issue a refresh token. An explicit `--scope` is sent exactly as given, without it. Credentials are not upgraded in place: re-run the command to get a grant with a refresh token.
- `sbx mcp auth status <name>` shows the granted scopes, the recorded defaults and the server's supported scopes, and highlights what was granted but never asked for.
- `sbx mcp load <name> --sandbox <s>` attaches a server to a running sandbox; connected sessions get a tool-list update without reconnecting, and the attachment survives a restart. `sbx mcp unload` detaches it again, which v0.48.0 added and which local sandboxes support only.
- `sbx mcp inspect <name>` shows the server's details and whether each of its secrets is set. It never shows a resolved value.

#### The gateway's own tools

Every sandbox gets a gateway with a few tools of its own: `mcp-exec` runs a named tool, `code-mode` builds an ephemeral tool that calls selected ones, and `<server>-authorize` starts an OAuth flow for a server that needs one. In dynamic mode `mcp-find`, `mcp-add` and `mcp-config-set` appear as well. Under organisation governance these are governed separately from the registered servers, as `MCP::Primordial` resources.

#### One connector, two doors

The workshop's fork ships a connector at `connectors/sbx-mcp/`. Its design point is that the read path and the write path are separate doors rather than one door with a lock on it.

| Tools | Always available | Write path |
| --- | --- | --- |
| Read | `sbx_status`, `sbx_list_sandboxes`, `sbx_inspect_sandbox`, `sbx_policy_check` | They only read. They cannot start, stop or remove anything. |
| Write | Hidden from `tools/list` unless the server was started with `SBX_MCP_ALLOW` naming them. | `sbx_run_test` additionally needs the command to match `SBX_MCP_ALLOWED_COMMANDS`. |

Registering it is the same command as any host stdio server:

**Host**

```
sbx mcp add sbx --command python3 --args "/path/to/connectors/sbx-mcp/sbx_mcp.py"
sbx mcp inspect sbx
```

When a write tool is refused, the refusal names the reason and lists what would be allowed. That is deliberate. A tool that is absent cannot be reviewed; a tool that answers "no, and here is the allowlist" can be read, argued with and audited. The write path is a decision you make once, in an environment variable, where the next reader can see it.

Sources: [MCP gateway](https://docs.docker.com/ai/sandboxes/mcp-gateway/) · [MCP access controls](https://docs.docker.com/ai/sandboxes/governance/access-controls/mcp/)

### Supply chain

The kits audit above found four supply-chain weaknesses in five kits. Docker has since shipped the tools that close them, so the weaknesses are now your choices rather than gaps in the product.

#### Two kit formats, and they do not mix

| | v2 | v3 |
| --- | --- | --- |
| Descriptor | `spec.yaml` with `schemaVersion: "2"` | YAML with `# syntax=docker/sandbox-kit:3` |
| Kinds | `sandbox`, `mixin` | `workload`, `mixin`, plus kit sets |
| Composition | `extends:`, `mixins:` | `requires:`, `provides:`, `integrates:`, `conflicts:` |
| Built with | `sbx kit pack` and `sbx kit push` | Docker Buildx |
| Published as | ZIP or OCI | OCI image |
| Signable | a local directory, a Git reference, an OCI artifact. A ZIP cannot carry a verifiable signature. | an OCI image only |
| Minimum sbx | v0.36 | v0.45 |

**v2 is not deprecated, but the built-in agent names still select v2 kits.** So `sbx run claude --kit ./some-v3-mixin` fails: you cannot add a v3 mixin to a v2 workload. And `sbx kit add` cannot add a v3 mixin to an existing v3 sandbox at all, because a v3 mixin set is fixed at creation. Pick a format per project and stay inside it.

#### Signing, and what a signature covers

**Host**

```
sbx kit sign ./my-kit/
sbx kit push ./my-kit/ ghcr.io/myorg/my-kit:1.0 --sign
sbx kit verify --certificate-identity release-bot@example.com --certificate-oidc-issuer https://accounts.google.com ./my-kit/
sbx kit provenance ghcr.io/myorg/my-kit:1.0
```

Sigstore and cosign compatible, keyless by default. **Provenance is unsigned unless you push with `--sign`**, which signs the manifest and its attestation together. A signature covers the kit's `spec.yaml` and its `files/` directory. It does not pin an image tag, and it does not check the bytes an install command downloads later. That last gap is the one the kits audit found, and it is why an embedded per-architecture hash is still the right answer for a download step.

#### The trust settings

| Setting | Default | What it decides |
| --- | --- | --- |
| `kit.allowedSources` | `["docker.io/"]` | Which remote sources a kit may come from. It replaces the whole list, and it matches on path-segment boundaries, so `github.com/myorg/` allows that organisation and not `github.com/myorg-other/`. |
| `kit.allowLocalKits` | `true` | Whether a local directory or a v2 ZIP may be loaded at all. |
| `kit.requireSignature` | `false` | Rejects unsigned kits, non-matching signatures, and ZIP kits outright. |
| `kit.trustedSigners` | Docker's own identities | Whose signature is accepted. Setting it does not by itself require a signature. |
| `kit.ignoreTransparencyLog` | `false` | For a private kit signed with `--tlog-upload=false`; it needs a timestamp authority instead of a transparency log entry. |

**This workshop hits that gate today.** `factory/sbxenv.yaml` pulls the ACR kit from a `git+https://github.com/shelajev/…` source, and the default allowlist is Docker Hub only. A fresh environment creation fails at the source policy before anything is built. Widening the allowlist is a decision with a footprint, so it is recorded in this host's `factory/README.md` rather than made silently. Read it before you change it.

#### What validation does not do

`sbx kit validate` checks structure. It does not run install commands, test that a host is reachable, or catch two kits that conflict. v0.49.1 added `--offline`, which checks a local directory or a ZIP without starting the daemon or signing in, so a lint step in a pull request no longer needs a login. It still does not grant permission to load the kit: that is a separate check against the trust settings.

Sources: [Kits v2](https://docs.docker.com/ai/sandboxes/customize/kits-v2/) · [SPEC-v2](https://github.com/docker/sbx-kits-contrib/blob/main/spec/SPEC-v2.md) · [release notes](https://github.com/docker/sbx-releases/releases)

### Skills, mounts and SSH

Three surfaces the earlier chapters never reach, because they arrived after the workshop was written.

#### The shared skills store

`sbx skills add anthropics/skills` installs skills from a Git repository. `sbx skills import` copies the ones already on this machine, checking `~/.agents/skills`, `~/.claude/skills`, `~/.config/opencode/skills`, `~/.copilot/skills`, `~/.cursor/skills` and `~/.factory/skills` in that order, taking the first copy of a name and warning about the rest. `--dry-run` previews without copying. The store lives under Application Support on macOS and is mounted read-only into every new sandbox by default.

**Host**

```
sbx skills ls
sbx skills import --dry-run
sbx settings set skills.defaultMode readonly
```

**A shared store is a shared trust boundary.** A sandbox mounted read-write can change the skills another sandbox later loads, including one mounted read-only. That does not reach the rest of your filesystem, but it does put those sandboxes in one boundary with each other. `--skills=off` keeps a sandbox out of it, which is what this workshop's own `factory/sbxenv.yaml` does.

#### Mounting into a running sandbox

`sbx mount` bind-mounts a host path into a sandbox that is already running, and `sbx umount` takes it away again. A bare host path mounts at the same absolute path inside the container, the same convention the create-time workspace mount uses, so paths in the sandbox match paths on your Mac. A target can be given explicitly, and `:ro` makes it read-only.

**Host**

```
sbx mount learn-01 "$HOME/Projects/example-project"
sbx mount learn-01 "$HOME/Projects/example-project":/workspace/data:ro
sbx umount learn-01 "$HOME/Projects/example-project":/workspace/data:ro
```

Mounts are idempotent and are restored after a stop and start. The mount policy is checked before the call reaches the daemon, using the effective read-only intent, so a policy that distinguishes read-only from read-write sees the same thing the bind mount does. For a stopped sandbox, `sbx umount` removes the saved mount so it is not restored.

#### SSH, and the editors that use it

`sbx setup ssh` writes a managed block into `~/.ssh/config` with `Host *.sbx` and a `ProxyCommand` that relays to the sandbox daemon over its socket. There is no key and no SSH server inside the sandbox: the daemon terminates the connection, and it accepts one only while you hold an active Docker login. The sandbox name is the hostname.

**Host**

```
sbx setup ssh
ssh learn-01.sbx
```

v0.48.0 added `sbx setup ssh --auto-create`, which registers connections in Codex and Claude Desktop on macOS and Windows. Each creates a dedicated sandbox on first use. Those sandboxes have **no host workspace mounted**, so you clone or copy the project into them, and the changes stay there until you push them somewhere or copy them out. `sbx setup ssh remove` takes the connections away again.

Editors and apps that speak remote-over-SSH can use the same hostname: VS Code, Cursor, Claude Desktop, ChatGPT, T3 Code and Orca. An SSH session starts in the sandbox workspace, matching `sbx exec`.

Sources: [share agent skills](https://docs.docker.com/ai/sandboxes/workflows/agent-skills/) · [editor integrations](https://docs.docker.com/ai/sandboxes/integrations/)

### Troubleshooting

Symptom first, then the fix. The last row is the one that ends most sessions: `sbx diagnose` reads the installation's own state and prints it as a table, JSON, YAML or a GitHub issue body.

| Symptom | What it is | The fix |
| --- | --- | --- |
| `sbx ls` shows a sandbox as "not responding" | The guest stopped answering. | `sbx stop` then `sbx start`. The files are kept. |
| A command hangs | The daemon is wedged, often behind an unresponsive Docker. | `sbx daemon restart` and try again. Then `sbx reset`. |
| "You are not authenticated to Docker" | No active login. | `sbx login` in your own terminal. In a script, sign in with a personal access token first. |
| The agent cannot install a package | Network policy, not the agent. | `sbx policy log`, then allow the host you need for that sandbox. If a rule is already there and it still fails, organisation governance is active and local allow rules do not apply. |
| A kit is refused with "source not in allowlist" | `kit.allowedSources` does not include it. | Add the source prefix, or a narrower one: `sbx settings set kit.allowedSources '["docker.io/","github.com/myorg/"]'`. |
| The agent cannot reach a service on your Mac | Sandboxes reach the host through `host.docker.internal`, and the proxy translates it to `localhost`, which the policy has to allow. | `sbx policy allow network localhost:11434`, then use `host.docker.internal`. `127.0.0.1` and LAN addresses never work. |
| Agent authentication fails | Usually the proxy's decision, not the agent's. | Read the `PROXY` column of `sbx policy log`. A row marked `transparent` was enforced without any credential injection. |
| MCP streams stall | HTTP/2 through a proxy that mangles it. | Register the server with `--disable-http2`. |
| "self-signed certificate in certificate chain" inside the sandbox | Your organisation's root certificate is not in the sandbox's trust store. | Copy the certificate in with `sbx cp`, install it with `sbx exec` and `update-ca-certificates`. Do not override `SSL_CERT_FILE`: it replaces the whole bundle and breaks the credential proxy. |
| Out of disk inside the sandbox | The root filesystem is 20 GB and the Docker data disk is 10 GB by default. | Raise them with `DOCKER_SANDBOXES_ROOT_SIZE` and `DOCKER_SANDBOXES_DOCKER_SIZE` before creating the sandbox; they cannot resize an existing one. |
| Filesystem operations are slow in a large checkout | Direct-mode virtualio-fs caching is not helping. | Create the sandbox with `DOCKER_SANDBOXES_ENABLE_VIRTIOFS_CACHE=0`. Clone mode always enables the cache. |
| Commits inside the sandbox are not signed | The forwarded SSH agent is not usable, or the tool is missing. | Check `ssh-add -L` on the host and `ssh.agentForwardingEnabled`. GPG and S/MIME keys are never available inside a sandbox. |
| The daemon will not start after a downgrade | Its database is a newer major version than the binary expects. | `sbx reset --preserve-secrets`. |
| Nothing above worked | | `sbx diagnose --format json` for the machine-readable form, or `sbx diagnose --format github-issue` for a body you can paste into an issue. Without a terminal, `--upload` saves the bundle and skips the upload, and a successful exit does not mean an upload happened. |

Sources: [troubleshooting](https://docs.docker.com/ai/sandboxes/troubleshooting/) · [release notes](https://github.com/docker/sbx-releases/releases)

### Hands-on path

Twelve exercises, each proving one idea by trying to break it. They work at any point in the workshop: every learning sandbox is named `learn-*`, publishes no ports (so nothing collides with 3102), and never edits your chapter files in `factory/`: exercises 4 and 5 add two folders there (`my-probe`, `learn-env`) and exercise 8 removes them. Run one at a time. The commands are grounded in the `--help` text of the rc2 binary, and where a spelling changed in v0.48.0 the block says so; none were run on your behalf. This page never runs them either: copy each block into your own terminal. The blocks carry no trailing comments, because zsh's default setting would pass a comment to the command as arguments.

#### Before you start

Sign in, then take a baseline so exercise 8 can show what outlived its sandbox. The strip at the top of this view tells you whether `sbx ls` is answering yet.

**Host**

```
cd ~/Projects/wad-sbx-workshop
sbx login
sbx policy ls --wide > /tmp/sbx-baseline-policy.txt
sbx secret ls        > /tmp/sbx-baseline-secrets.txt
sbx mcp ls           > /tmp/sbx-baseline-mcp.txt
```

### Exercise 1

#### A separate kernel

Prove the sandbox is a VM and that root inside it isn't the boundary.free · no model

##### Steps

**Host**

```
sbx run shell "$(pwd)/sample-app" --name learn-01 --skills off --cpus 2 --memory 4g
```

On first run it asks for a network preset. The docs recommend **Balanced** (default deny, common dev sites allowed). [corrected] The workshop itself doesn't name one.

**Sandbox**

```
uname -sr; nproc; whoami
sudo -n true && echo sudo-ok
docker version --format '{{.Server.Version}}'
echo 'I live in the VM' > ~/vm-only.txt
exit
```

**Host**

```
uname -sr
sbx ls
sbx stop learn-01
sbx run --name learn-01
```

Back inside after the restart, run `cat ~/vm-only.txt`, then `exit`.

##### What you should see

- Linux inside, Darwin outside; 2 CPUs, not your core count.
- User `agent` with working `sudo`, and a Docker server with no Docker Desktop installed.
- The file in `~` survives stop and start: VM state persists until `sbx rm`.

##### Try to break it

Inside, `sudo cat /etc/shadow` works, because root inside the VM is not the boundary. Listing your home directory shows only the path down to the mounted workspace.

##### Clean up

Keep `learn-01` for exercises 2 and 3.

### Exercise 2

#### Files: the one hole that matters most

See the mount is live and read-write, including things git won't show you.free · no model

##### Steps

**Host**

```
sbx run --name learn-01
```

**Sandbox**

```
pwd
echo 'Hello from SBX' > sandbox-message.txt
cat ../host-only.txt
touch .git/hooks/zz-demo-from-sandbox
```

**Host · second tab**

```
cat sample-app/sandbox-message.txt
cat host-only.txt
ls sample-app/.git/hooks | grep zz-demo
git -C sample-app status --short
```

##### What you should see

- `pwd` shows the same absolute path as on your Mac, and the new file appears on the host instantly.
- `../host-only.txt` fails inside but reads fine outside: only the mount crosses.
- The hook file exists on your Mac, and `git status` doesn't list it. That's the lesson.

##### Try to break it: clone mode

**Host**

```
sbx create --clone --name learn-clone shell "$(pwd)/sample-app"
sbx run --name learn-clone
```

**Sandbox**

```
echo x > clone-only.txt
ls /run/sandbox/source
touch /run/sandbox/source/x
```

The `touch` fails: the source is mounted read-only. On the host, `ls sample-app/clone-only.txt` finds nothing and `git -C sample-app remote -v` shows a `sandbox-learn-clone` remote. Clone mode stops modification, not inspection: untracked files such as `.env` are still readable.

##### Clean up

**Sandbox · learn-01**

```
rm sandbox-message.txt .git/hooks/zz-demo-from-sandbox
```

**Host**

```
sbx rm learn-clone
```

### Exercise 3

#### Network: default deny, observed

Watch the proxy allow, block and log, and see this dashboard stay out of reach.free · no model

##### Steps

**Host**

```
sbx policy ls
sbx policy check network github.com
sbx policy check network example.com
sbx policy check network --sandbox learn-01 localhost:7878
sbx run --name learn-01
```

**Sandbox**

```
curl -sS -o /dev/null -w '%{http_code}\n' https://github.com
curl -sS --max-time 10 -o /dev/null -w '%{http_code}\n' https://example.com
```

**Host**

```
sbx policy log learn-01 --limit 20
sbx policy allow network --sandbox learn-01 example.com
sbx policy check network example.com
```

After the `allow`, retry the second `curl` inside: it now succeeds. The last command still answers Denied, because the rule belongs to this sandbox only.

##### What you should see

- Allowed hosts work, unlisted ones fail, and the log names the blocked host.
- A per-sandbox rule changes behaviour at once, without a restart, and only for that sandbox.

##### Try to break it

**Sandbox**

```
curl -sS --max-time 5 http://host.docker.internal:7878/
curl -sS --max-time 5 http://host.docker.internal:11434/api/tags
ping -c1 1.1.1.1
```

All three fail. This dashboard and Ollama become reachable only if you allow `localhost:7878` or `localhost:11434`, which you shouldn't. Then on the host, `sbx policy deny network --sandbox learn-01 example.com`: the retry fails, because deny wins.

##### Clean up

**Host**

```
sbx policy ls learn-01 --wide
sbx policy rm network --sandbox learn-01 --resource example.com
sbx policy ls learn-01 --wide
```

If you also added the deny rule and the second listing still shows it, run the `policy rm` line once more (or pass `--id` from the listing) until neither rule is listed. Only then remove the sandbox:

**Host**

```
sbx rm learn-01
```

### Exercise 4

#### Write and validate your own kit

The my-probe mixin from the anatomy section: build it, apply it, break it.free · no model

##### Steps

Create `factory/my-probe/spec.yaml` with the YAML from [Anatomy of a kit](#sbxg-kit). `factory/` is gitignored by the workshop.

**Host**

```
sbx kit validate factory/my-probe --json
sbx kit validate factory/my-probe --kit-arg 'probe_host=BAD HOST' --json
sbx run shell "$(pwd)/sample-app" --name learn-kit --skills off --cpus 2 --memory 4g --kit ./factory/my-probe
```

The second `validate` is rejected by the kit's `pattern`; that is the point of it.

**Sandbox**

```
probe
probe ifconfig.me
echo "$PROBE_HOST | $PROBE_NOTE"
cat ~/.my-probe-installed
exit
```

**Host**

```
sbx policy ls learn-kit --source kit --type network --wide
sbx policy check network example.com
sbx policy check network --sandbox learn-kit example.com
```

##### What you should see

- The kit's allow shows up as a kit-sourced rule scoped to `learn-kit` only: globally Denied, for this sandbox Allowed.
- `probe` reaches example.com and is blocked from ifconfig.me.

##### Try to break it

- Add a `sandbox:` block to the mixin and validate: rejected, only valid for kind `sandbox`.
- Replace `permissions:` with the v1 `network: allowedDomains:`: rejected, unknown field.
- Allow `"**.example.com"`: it validates with no warning, although the docs say enforcement is pending.

Whether the `shell` agent accepts a v2 mixin is [inferred]; the docs show `--kit` with `claude`.

##### Clean up

**Host**

```
sbx rm learn-kit
```

Keep `factory/my-probe`: exercise 5 uses it.

### Exercise 5

#### Compose it into an environment file

Declare the sandbox, read the plan, and learn when changes take effect.free · no model

##### Steps

Create `factory/learn-env/sbxenv.yaml`:

**factory/learn-env/sbxenv.yaml**

```
schemaVersion: "1"
name: "${{ env.args.name }}"
agent: shell
workspace: ../../sample-app
args:
  name:
    default: learn-env
  probe_host:
    default: example.com
sandboxOptions:
  skills: off
  cpus: 2
  memory: 4g
kits:
  - source: ../my-probe
    args:
      probe_host: "${{ env.args.probe_host }}"
```

**Host**

```
sbx env plan factory/learn-env
sbx env plan factory/learn-env --env-arg probe_host=httpbin.org
sbx env run factory/learn-env
```

Compare the two plans, and read the plan `env run` prints before you approve it. Inside, run `probe`, then `exit`. Now change the default `probe_host` to `httpbin.org` in the file and run `sbx env run factory/learn-env` again: `probe` still tests example.com. Then `sbx env rm factory/learn-env` and `sbx env run factory/learn-env` once more, and it uses httpbin.org.

##### What you should see

The plan names the kit, its arguments and the mount. Kit changes are pending until the next *create*: "Changes to workspaces, kits, ports, secrets, bindings, and sandboxOptions take effect only when the sandbox is next created."

##### Try to break it

Make `name` `required: true` (as the workshop's own factory file does), omit `--env-arg`, and watch the plan refuse. Then restore `default: learn-env`. The clean-up passes `--env-arg name=learn-env`, so it works either way, but a plain `sbx env run factory/learn-env` would keep being refused.

This file was not validated when the guide was written (`sbx env plan` needs a login). [inferred]

##### Clean up

Leave `learn-env` in place: exercise 8 inspects it, then removes it together with both folders. If you are stopping here instead:

**Host · only if you skip exercise 8**

```
sbx env rm factory/learn-env --env-arg name=learn-env
rm -rf factory/learn-env factory/my-probe
```

### Exercise 6

#### Secrets: can't read, can use

Watch a fake secret stay out of the VM, then watch where that protection ends.free · no model

[corrected] The first draft set the secret after creating the sandbox and expected an env var to appear; the docs say an existing sandbox doesn't get one. This version uses the placeholder directly: the proxy swaps it wherever it appears in a request to the bound host. Custom secrets are experimental.

##### Steps

**Host**

```
sbx create --name learn-sec shell "$(pwd)/sample-app" --skills off --cpus 2 --memory 4g
sbx policy allow network --sandbox learn-sec "httpbin.org,postman-echo.com"
sbx secret set-custom --sandbox learn-sec --host httpbin.org --env DEMO_TOKEN \
  --placeholder demo-{rand} --value not-a-real-secret-42
sbx secret ls --sandbox learn-sec
sbx run --name learn-sec
```

The value is fake on purpose. Note the placeholder `set-custom` prints (it starts with `demo-`): you paste it into the commands below.

**Sandbox · replace demo-XXXX with the printed placeholder**

```
env | grep -c not-a-real-secret
sudo grep -rs not-a-real-secret /etc /home /run /tmp | head
curl -s https://postman-echo.com/headers -H "Authorization: Bearer demo-XXXX"
```

##### What you should see

No trace of the real value anywhere in the VM, and postman-echo (not bound) receives only the placeholder.

##### Try to break it

**Sandbox**

```
curl -s https://httpbin.org/headers -H "Authorization: Bearer demo-XXXX"
echo $SSH_AUTH_SOCK; ssh-add -l
```

httpbin echoes back the headers it received, so the *real* value can come back into the sandbox. That's the whole lesson: bind secrets only to hosts you trust not to reflect them. And SSH agent forwarding is on: processes can ask your host agent to sign even though they can't read keys. Compare `sbx policy log learn-sec --limit 10` on the host: only the forward proxy path injects.

Never test with a real `sbx secret set anthropic` key: Claude sandboxes would then bill the API.

##### Clean up

**Host**

```
sbx rm learn-sec
sbx secret ls
```

Removing the sandbox also deletes the secrets scoped to it; the listing confirms that.

### Exercise 7

#### MCP: scoped access to a host tool

Give an agent the Beans backlog as two read-only tools, not a folder.uses Claude subscription

##### Steps

**Host**

```
cat scripts/beans-mcp
./scripts/beans list
sbx mcp add learn-beans-ro --command bash --args "\"$(pwd)/scripts/beans-mcp\""
sbx mcp ls
sbx mcp inspect learn-beans-ro
sbx run claude "$(pwd)/sample-app" --name learn-mcp --skills off --cpus 2 --memory 4g --static-mcp learn-beans-ro
```

The registration leaves out `--allow-notes`, so the server is read-only. In Claude: `/login` if asked, then `/mcp` to see the server and its tools (`get_task`, `list_tasks`, no `add_task_note`). Ask: "Use the beans MCP tools to list tasks and show wad-102. Do not change anything." Then `!ls "$(dirname "$PWD")/.local"` fails: the session starts in the mounted `sample-app`, and the workshop folder one level up, where the backlog lives, is not mounted. (Inside the VM `~` is `/home/agent`, so a `~/Projects/…` path would fail whatever was mounted and prove nothing.)

**Host · second tab**

```
ps aux | grep '[b]eans-mcp'
```

That process is running as you, outside the VM.

##### Try to break it

Ask Claude to "append a note to wad-102" (no such tool in read-only mode) or "use the MCP tools to read ~/.ssh/config" (no such tool). Then think about dynamic mode: without `--static-mcp` the gateway offers `mcp-find` and `mcp-add`, and the agent could attach *any* server registered on this Mac. Keep registrations few and prefer static mode.

##### Clean up

**Host**

```
sbx rm learn-mcp
sbx mcp rm learn-beans-ro
```

Registrations are host-global and outlive sandboxes, so this one is removed explicitly.

### Exercise 8

#### Inventory: what you created, what outlived it

A sandbox is a bundle of separately owned resources. Audit them.free · no model

##### Inspect a live sandbox

Use `learn-env` from exercise 5. If you removed only the sandbox, recreate it with `sbx env run factory/learn-env`, then `exit`. If you removed the two folders as well, repeat exercise 5's setup first.

**Host**

```
sbx ls
sbx inspect learn-env
sbx inspect learn-env --json | jq 'keys'
sbx policy ls learn-env --wide
```

Map each `inspect` field (agent, kits, auth mode, workspace and mounts, network policy, secrets, ports, sessions) to the exercise that created it. Its output contains host paths: review before sharing. To watch lifecycle events, run `sbx events sandbox.lifecycle` in another tab while you do the next step.

##### Remove what is left

**Host**

```
sbx env rm factory/learn-env --env-arg name=learn-env
rm -rf factory/learn-env factory/my-probe
```

##### The residue audit

**Host**

```
sbx ls
sbx prune --dry-run
diff /tmp/sbx-baseline-policy.txt  <(sbx policy ls --wide)
diff /tmp/sbx-baseline-secrets.txt <(sbx secret ls)
diff /tmp/sbx-baseline-mcp.txt     <(sbx mcp ls)
ls factory
git -C sample-app status --short
git -C sample-app remote -v
```

[corrected] Diff against the baseline you took before exercise 1: global preset rules and earlier registrations legitimately outlive sandboxes. Anything *new* in the diff is residue from these exercises. `ls factory` should list only your chapter files, with no `my-probe` or `learn-env`. The Workshop view next door should also show no `learn-*` sandboxes left.

##### Clean up

**Host**

```
rm /tmp/sbx-baseline-policy.txt /tmp/sbx-baseline-secrets.txt /tmp/sbx-baseline-mcp.txt
```

### Exercise 9

#### Your own repo, safely

Give the agent a disposable copy on a branch, then review it like an untrusted PR.uses Claude subscription

##### Steps

**Host · pick a small repo**

```
mkdir -p ~/sbx-scratch
git clone --no-hardlinks ~/Projects/<small-repo> ~/sbx-scratch/<small-repo>
git -C ~/sbx-scratch/<small-repo> switch -c sbx/experiment
sbx run claude ~/sbx-scratch/<small-repo> --name learn-own --skills off --cpus 4 --memory 8g
```

##### Afterwards, review before you run anything there

**Host**

```
git -C ~/sbx-scratch/<small-repo> log --oneline -5
git -C ~/sbx-scratch/<small-repo> diff main...
ls -la ~/sbx-scratch/<small-repo>/.git/hooks
```

Also read `.github/`, `.claude/`, `Makefile` and `package.json` scripts. Why the throwaway: a direct mount lets the agent plant hooks and CI that run later on your Mac; a fresh clone leaves out untracked files like `.env`; `--no-hardlinks` matters because a local clone hard-links `.git/objects`, and hard links reach outside the workspace.

Don't mount the live `~/Projects/ai-dashboard`: it serves this page. The workshop's chapter 7 flow is the team version of this exercise; it needs the kits, team and MCP wiring from chapters 3 to 6. [corrected]

##### Clean up

**Host**

```
sbx rm learn-own
rm -rf ~/sbx-scratch/<small-repo>
rmdir ~/sbx-scratch
```

### Exercise 10

#### Automation: the same three commands, unattended

Create, execute, remove — and read the exit status the way a script has to.free · no model

##### Steps

Exercises 1 to 9 attached a person to a terminal. This one does not: the sandbox is created without an agent session, a command runs inside it, and the sandbox goes away afterwards. That is the shape every automated run takes.

**Host**

```
sbx create --name learn-auto --clone shell "$(pwd)/sample-app"
sbx ls --json
sbx exec learn-auto -- uname -sr
sbx exec learn-auto -- sh -c 'exit 3'
echo "exit status was $?"
```

The `echo` line is the important one. `sbx exec` returns the command's own exit status, so a script can branch on it. Note the order: `sbx exec` has to run before the `echo`, or the status you read is the status of something else.

##### What you should see

- `sbx create` prints a summary and returns without opening a session.
- `sbx exec` starts the sandbox if it was stopped, runs the command, and exits with the command's status: 3 in this case.
- `sbx ls --json` now lists `learn-auto`.

##### Try to break it

Run `sbx exec learn-auto -- sh -c 'exit 3'` again, and this time pipe it: `sbx exec learn-auto -- sh -c 'exit 3' | cat`. The status you read afterwards is now `cat`'s, not the command's. A pipeline hides the status of the first command, which is why a script that reads `$?` after a pipe is reading the wrong thing.

Then read the JSON: `sbx ls --json`. On v0.45 it is wrapped in an object with a `sandboxes` key; from v0.48.0 it is a bare array. The same script has to survive both, and [Automation](#sbxg-automation) shows the eight-line normaliser that does.

##### Clean up

**Host**

```
sbx rm learn-auto
```

### Exercise 11

#### Runtime mounts and the shared skills store

Give a running sandbox a host path, prove it is read-only, take it away again.free · no model

##### Steps

A mount can be added to a sandbox that is already running, without recreating it. That is new enough to be missing from the workshop chapters.

**Host**

```
sbx create --name learn-mount shell "$(pwd)/sample-app"
sbx mount learn-mount "$(pwd)/chapters/examples/hello-kit":/mnt/kit:ro
sbx exec learn-mount -- ls -la /mnt/kit
```

The target path is given explicitly here, so the mount lands at `/mnt/kit` inside the container rather than at the host's absolute path. Leaving the target off mounts it at the same absolute path, which is what the workspace mount does.

##### What you should see

`ls -la /mnt/kit` lists the kit's files. The mount is live: the same files are on your Mac, at the path you named.

##### Try to break it

**Host**

```
sbx exec learn-mount -- sh -c 'touch /mnt/kit/new-file'
sbx exec learn-mount -- sh -c 'cat /mnt/kit/spec.yaml' | head -5
```

The first command fails with a read-only filesystem error, because the mount said `:ro`. The second one succeeds: the same mount lets the sandbox read a file it cannot change. A read-only mount is a one-way door, and it is the direction you want for anything that came from your Mac.

##### Take the mount away

**Host**

```
sbx umount learn-mount "$(pwd)/chapters/examples/hello-kit":/mnt/kit:ro
sbx exec learn-mount -- ls -la /mnt/kit
```

The second command now fails: the path is gone from inside the sandbox. `sbx umount` has to be given the same target the mount used, and the host part is resolved against the directory you are in, so run it from the same place you mounted from.

##### The other surface: shared skills

**Host**

```
sbx skills ls
sbx skills import --dry-run
```

The first command lists the skills in the shared store, the second previews what would be imported from the agents installed on this Mac without copying anything. The store is mounted read-only into new sandboxes by default, so a skill in it reaches every agent and no agent can change it for the others. A sandbox created with `--skills=readwrite` breaks that, which is why the workshop's own environment file uses `skills: off`.

##### Clean up

**Host**

```
sbx rm learn-mount
```

### Exercise 12

#### Ask the daemon what it thinks

Diagnose, read the policy, read the settings — the three questions worth asking before anything else.free · no model

##### Steps

Three commands, none of which changes anything, and between them they answer most of the questions you are likely to have.

**Host**

```
sbx version --json
sbx diagnose --format json
sbx policy ls --wide
```

The first tells you which build you are on, which decides which flag spellings and which output shapes the rest of this guide has to use. The second is the installation's own health check: the CLI binary, the daemon, whether their versions agree, the storage directories, the authentication, and — from v0.49.1 — whether the global network policy has been initialised at all. The third is the effective policy: every rule, where it came from, what it applies to and whether it allows or denies.

**On a build older than v0.48** the diagnose flag is `--output json` rather than `--format json`, and the two are not interchangeable. The error message names the accepted values; the release table in [What changed](#sbxg-whatsnew) has the full list of renames.

##### What you should see

- `sbx version --json` names a client version, a revision and a server state.
- The diagnose document is a list of named checks, each with a result and, usually, a remedy.
- The policy listing separates rules by source: local, kit, or a governance profile, and each row says which sandbox or profile it applies to.

##### Try to break it

Look for a rule whose `APPLIES TO` column says something other than `all`: that is a rule scoped to one sandbox, and it is the reason an allow worked in exercise 3 without becoming a global allowance. Then look for a rule with a decision of `deny`. Deny wins, everywhere, in every layer — and it is the rule to check first when something you expected to be allowed is not.

Two more read-only commands are worth knowing. `sbx policy log learn-01 --limit 5` shows the last decisions, including a `PROXY` column that says whether a credential was injected. `sbx settings list` shows every setting, its value, where the value came from and whether a change needs a daemon restart. `sbx events sandbox.lifecycle` streams lifecycle events until you interrupt it, which is worth watching in a second terminal while you do something else.

##### Clean up

Nothing to clean up: all three commands only read. If you ran exercise 1 and still have `learn-01`, its clean-up is in exercise 8's residue audit.

### Applying it to your setup

Several problems this dashboard already tracks are this field under other names.

- **The agent lanes run as you.** Claude Code, Codex and the local `omp` lane (approval mode `write`) sit on rung 1 unless sandboxed: they can read what your user can read. Codex's and Claude Code's built-in sandboxes lift them to rung 7; sbx would lift a lane to rung 6.
- **Isolating executor test runs** maps to mountless or clone-mode sandboxes: `sbx create` with no path shares nothing, `--clone` shares read-only.
- **Egress allowlisting for cloud calls** is exactly what sbx's proxy policy does, including the trap that broad allowed hosts remain exfiltration paths.
- **Local services**, this dashboard on :7878 and Ollama on :11434, are unreachable from a sandbox until you allow `localhost:PORT`. Exercise 3 shows it.
- **Following your progress:** the Workshop view infers chapter progress and reports whether the daemon is up; the Learning view holds the rules this workshop has taught the system, with their evidence.

#### How this gets used for sandbox testing

The plan is to run the dashboard's own test suite inside a disposable sandbox rather than on the working Mac, and to keep the evidence. Four pieces, all in the workshop fork:

| Piece | What it is | Why it exists |
| --- | --- | --- |
| `connectors/sbx-runner/` | A runner that creates a sandbox, executes a command, writes a receipt and removes the sandbox. | Its receipt names the sbx build, the command, a digest of the tree it ran against, the exit status and the timings. An exit code alone cannot tell you later whether it was the same code. |
| `connectors/sbx-mcp/` | An MCP server exposing the sandbox state to an agent, with reads always on and writes behind an environment gate. | An agent lane can ask what exists without being given a shell on the host. |
| `connectors/kits/dashboard-tests/` | A kit that installs the Python toolchain and writes a runner script that leaves a tree digest and JUnit XML behind. | The test run produces its own evidence inside the sandbox, not just a verdict. |
| `docs/howto/` | Eight how-to guides over a fact ledger that records where every claim came from. | The ledger is the part that matters: the CLI moved twice in three months, and prose written from memory was already wrong when it was written. |

Two rules make it work on this Mac. The runner adapts to the installed sbx: it picks the flag spelling that version understands and normalises both the old wrapped and the new bare list shapes, so one recipe survives the upgrade. And the workspace is cloned into the sandbox rather than mounted, so a test that writes a file, or a fixture that trips over a path, cannot touch the checkout that serves this page.

The known limit is worth stating plainly: a clone-mode sandbox runs the suite against a copy, so a test that depends on the working tree's uncommitted state sees the committed state. The receipt carries the tree digest precisely so that difference is visible instead of silent.

### Sources and corrections

Four researchers read primary sources and the local binary on 25 September 2026; ten agents in total, with an adversarial fact-check of 62 load-bearing claims: 39 supported, 20 tightened, 3 corrected. The three outright errors, all fixed above:

- **The VM monitor** is Docker's Sailor (`libsailor.dylib`) on Hypervisor.framework, not libkrun.
- **herdr**'s upstream release does publish per-binary sha256 digests; the kit just doesn't check them.
- **The secrets exercise** had its steps in an order that could not work.

Twenty more claims were tightened: credential injection is the default, not an absolute; `sbx create` differs from `run`; v2 kit kinds are `sandbox`/`mixin` (`workload` is v3); the Firecracker numbers are as of the 2020 paper; gVisor's file access defaults to directfs; and others.

#### The second pass, on 22 October 2026

The guide was checked again against the release notes, the documentation and the local binary. The important outcome was not a new error but a **stale version**: the version advice above had named v0.45.0 and v0.45.1 as the latest, when v0.46.0 through v0.49.2 had shipped in the meantime, each carrying proxy or filesystem security fixes. The advisory that matters is the eMudhra certificate compromise, which needed its own section rather than a footnote.

Two claims about the surface itself were wrong in the same way. The guide implied that `sbx ls --json` had a stable shape; it changed in v0.48.0, and a parser written against the old shape reports an empty list rather than an error. And the flag list was incomplete: `sbx exec`, `sbx cp`, `sbx mount`, `sbx umount`, `sbx skills`, `sbx setup ssh`, `sbx diagnose` and the kit signing commands were all present in the pinned binary and missing from the guide. [What changed](#sbxg-whatsnew), [Automation](#sbxg-automation), [Connectors](#sbxg-connectors), [Supply chain](#sbxg-supply) and [Skills, mounts and SSH](#sbxg-surface) are the result.

The claim this guide got right, and kept, is the one about how long it took: the CLI moved twice in three months, so anything written from memory was wrong when it was written. The workshop fork now keeps a fact ledger at `docs/howto/00-verified-facts.md` that records where every claim came from, and the how-to guides cite it instead of repeating it.

#### Read next

- [Docker Sandboxes: security](https://docs.docker.com/ai/sandboxes/security/), then [isolation](https://docs.docker.com/ai/sandboxes/security/isolation/) and [architecture](https://docs.docker.com/ai/sandboxes/architecture/)
- [Kits v2](https://docs.docker.com/ai/sandboxes/customize/kits/) and the [SPEC-v2](https://github.com/docker/sbx-kits-contrib/blob/main/spec/SPEC-v2.md) with its authoring pitfalls
- [Credentials and proxy injection](https://docs.docker.com/ai/sandboxes/configuration/credentials/) · [MCP gateway](https://docs.docker.com/ai/sandboxes/mcp-gateway/)
- [Firecracker: Lightweight Virtualization for Serverless Applications](https://www.usenix.org/system/files/nsdi20-paper-agache.pdf) (NSDI 2020)
- [gVisor architecture](https://gvisor.dev/docs/) · [namespaces(7)](https://man7.org/linux/man-pages/man7/namespaces.7.html) · [cgroups(7)](https://man7.org/linux/man-pages/man7/cgroups.7.html)
- [Claude Code sandboxing](https://code.claude.com/docs/en/sandboxing) · [sandbox environments](https://code.claude.com/docs/en/sandbox-environments) · [Codex sandboxing](https://learn.chatgpt.com/docs/sandboxing)
- [OWASP LLM01: prompt injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) · [Saltzer & Schroeder, The Protection of Information in Computer Systems](https://www.cs.virginia.edu/~evans/cs551/saltzer/)
- [The sbx release notes](https://github.com/docker/sbx-releases/releases) — the primary source for every version claim above
- The workshop: [shelajev/wad-sbx-workshop](https://github.com/shelajev/wad-sbx-workshop), and the fork carrying this guide's how-to layer and connectors
