# How-to guides

## What this layer is

The workshop chapters in `chapters/` teach you to build a factory with Docker
Sandboxes. This directory is the reference layer: task-shaped guides for the
things you hit when you stop following the script and start operating.

Everything in this directory is derived from one file,
[`00-verified-facts.md`](00-verified-facts.md), which holds a fact ledger
checked against primary sources — the release bodies in `docker/sbx-releases`,
the `--help` output of the `sbx` binary installed on this Mac, and pages on
`docs.docker.com`. Each guide cites the ledger rather than restating it, so
when a fact changes, one file changes.

The reason the ledger exists: the `sbx` binary on this Mac is **v0.45.0-rc2**,
and three minor releases came out after it, including v0.48.0, which renamed
flags and changed the shape of several JSON documents. Recollection is not
trustworthy across that gap. Neither is a blog post.

**If a guide and the ledger disagree, the ledger is right.** Where a guide
states something the ledger does not contain, it is marked *not verified in
the ledger* — usually an observation from the local binary's help text, and
always worth confirming.

## Reading order

Read `00-verified-facts.md` first if you have not. It is 490 lines and it is
the map.

Then, if you are working on this machine right now:

1. **[01-versions-and-upgrading.md](01-versions-and-upgrading.md)** — you are
   on v0.45.0-rc2 and the target is v0.49.2. Start here; everything else
   assumes you know which version you have.
2. **[03-structured-output-and-parsers.md](03-structured-output-and-parsers.md)**
   — if you write any script that reads `sbx` output. Do this before you
   automate anything.
3. **[02-automated-sandbox-testing.md](02-automated-sandbox-testing.md)** —
   running a test suite with no human present.

If you are building rather than operating:

4. **[05-kits-and-supply-chain.md](05-kits-and-supply-chain.md)** — kits,
   from `spec.yaml` anatomy to the `kit.allowedSources` gate this workshop
   actually hits.
5. **[04-mcp-connectors.md](04-mcp-connectors.md)** — the MCP surface, and
   the connector in `connectors/sbx-mcp/`.
6. **[06-skills-and-workspace.md](06-skills-and-workspace.md)** — the shared
   skills store and the four workspace modes.
7. **[07-mounts-ssh-and-ports.md](07-mounts-ssh-and-ports.md)** — runtime
   mounts, SSH, and published ports.

Keep **[08-troubleshooting.md](08-troubleshooting.md)** open in a second tab.

## The files

| File | What it is for |
| --- | --- |
| [00-verified-facts.md](00-verified-facts.md) | The fact ledger. Every other file in this directory cites it. Read this before you trust anything here. |
| [01-versions-and-upgrading.md](01-versions-and-upgrading.md) | Moving from v0.45.0-rc2 to v0.49.2: what changed per release, how to find the v0.48.0 breakages in your own scripts, the eMudhra CA compromise, and a compatibility shim that works on both spellings. |
| [02-automated-sandbox-testing.md](02-automated-sandbox-testing.md) | Running a test suite inside a disposable sandbox without a human: create → exec → remove, PAT authentication, exit codes, the stdout/stderr split, timeouts, JUnit XML, and a worked example using `connectors/sbx-runner/`. |
| [03-structured-output-and-parsers.md](03-structured-output-and-parsers.md) | Parsing `sbx` output safely across versions: the v0.48.0 shape changes, `--format` versus `--json`, the mutual exclusivity rule, why scraping human-readable output is a bug, and a worked normaliser with tests. |
| [04-mcp-connectors.md](04-mcp-connectors.md) | The MCP surface: `sbx mcp add` and its flags, `catalog`, `auth` and the scope precedence rules, `load` and `unload`, static versus dynamic mode, the built-in gateway tools, host stdio servers versus container ones, and the connector in `connectors/sbx-mcp/`. |
| [05-kits-and-supply-chain.md](05-kits-and-supply-chain.md) | Kits in depth: v2 versus v3, the `spec.yaml` anatomy, the argument pattern rule and raw text splicing, setup step order, the chown rule, idempotence, what a signature covers, the trust settings, the `kit.allowedSources` gate, and the kit in `connectors/kits/dashboard-tests/`. |
| [06-skills-and-workspace.md](06-skills-and-workspace.md) | The shared skills store: `add`/`import`/`update`/`rm`, the host source directories and their order, the store path on macOS, the three mount modes and the trust boundary they create, and the four workspace modes with their git consequences. |
| [07-mounts-ssh-and-ports.md](07-mounts-ssh-and-ports.md) | Runtime mounts and their idempotence, restore-on-restart and policy check; what the managed SSH config actually does; port publishing with the loopback default, the protocol suffixes and the ephemeral-port trap. |
| [08-troubleshooting.md](08-troubleshooting.md) | A symptom → fix table: the daemon, hung commands, corrupted state, missing workspaces, package installs, kit source gates, SSH, non-HTTP traffic, host services, agent authentication and the `PROXY` column, certificates, disk size, slow filesystems, Git ownership, and the last-resort state wipes. |

## Conventions used here

- **You** is the reader. Second person, present tense, no hedging.
- Every command block runs as written. Where a value is genuinely yours to
  fill in, it is spelled out in capitals and explained in the text around it.
- Where a fact is long, the guide points at the ledger's table instead of
  repeating it. Where a fact is short and you need it in place, it is
  repeated.
- Anything a guide asserts that the ledger does not contain is marked
  **not verified in the ledger**, in place, at the point of use.
- Each guide opens with what it is for and a "Before you start" block, and
  closes with a "What this does not cover" section that names its limits.

## Related material outside this directory

| Path | What it is |
| --- | --- |
| `connectors/sbx-runner/` | The version-compatible sandbox test runner. It reads the installed `sbx` version once, picks the spelling that version understands, normalises the shapes it gets back, and writes a receipt. |
| `connectors/sbx-mcp/` | The MCP stdio server that exposes Docker Sandboxes to an agent, with a read/write separation enforced by `SBX_MCP_ALLOW` and `SBX_MCP_ALLOWED_COMMANDS`. |
| `connectors/kits/dashboard-tests/` | A v2 kit that provisions a Python test toolchain and writes a runner that produces a tree digest and JUnit XML. |
| `factory/` | This machine's workshop environment: `sbxenv.yaml`, two small kits, and the `README.md` that records the `kit.allowedSources` finding. |
| `chapters/` | The workshop walkthroughs. Start at `chapters/00-setup/README.md`. |
