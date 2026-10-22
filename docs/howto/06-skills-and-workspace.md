# Skills and workspace modes

## What this is for

Two things decide how much an agent in a sandbox can see and change: the
**skills store**, which is a shared directory of agent instructions mounted
into every sandbox, and the **workspace mode**, which decides whose files the
agent works on and whether the agent can touch them.

Get the workspace mode wrong and a test run rewrites your working tree. Get
the skills mode wrong and two sandboxes end up in one trust boundary. Both
are decisions you make once, at creation, and cannot change afterwards.

## Before you start

- The `sbx` CLI on your `PATH` and a Docker login.
- A repository, if you intend to use clone mode.
- Read [`00-verified-facts.md`](00-verified-facts.md) §10 (the trust
  boundary), §11 (git workflows) and §12 (the skills store).

```bash
sbx version --json
sbx skills --help
```

## 1. The shared skills store

A skill is a directory containing a `SKILL.md`. The store is a single
directory on the host that gets mounted into sandboxes, so one copy of a
skill serves every sandbox and every agent.

### Where the store lives

On macOS:

```
~/Library/Application Support/com.docker.sandboxes/sandboxes/agent-skills
```

`sbx skills import --dry-run` prints the exact path it would use. On this
Mac it reads:

```
Dry run: 4 skill(s) would be imported into /Users/michaelhamanaka/Library/Application Support/com.docker.sandboxes/sandboxes/agent-skills
```

`sbx reset` clears the store.

### `sbx skills add`

Installs skills from a Git repository. The repository must contain one or more
valid `SKILL.md` files.

```bash
# A Git URL
sbx skills add https://github.com/owner/repo.git

# The GitHub owner/repository shorthand
sbx skills add owner/repo
```

| Flag | Meaning |
| --- | --- |
| `-s, --skill <name>` | Install only the named skill. Repeatable, or comma-separated. With no `--skill`, all discovered skills are installed. |
| `-f, --force` | Overwrite existing skills without prompting. |

Replacing an already-installed skill **requires confirmation**. Use `--force`
in a script.

Skills installed with `sbx skills add` can later be refreshed with
`sbx skills update`.

### `sbx skills import`

Copies skills out of the host's agent directories.

```bash
sbx skills import --dry-run
sbx skills import
```

| Flag | Meaning |
| --- | --- |
| `--dry-run` | Preview what would be imported without copying anything. |
| `-f, --force` | Overwrite an existing stored entry without confirmation. |

The source directories, **in order**:

| Order | Directory |
| --- | --- |
| 1 | `~/.agents/skills` |
| 2 | `~/.claude/skills` |
| 3 | `~/.config/opencode/skills` |
| 4 | `~/.copilot/skills` |
| 5 | `~/.cursor/skills` |
| 6 | `~/.factory/skills` |

**The first copy wins.** The others are skipped with a warning. A skill named
`graphify` that exists in both `~/.agents/skills` and `~/.claude/skills` is
imported once, from `~/.agents/skills`, and the `~/.claude/skills` copy is
skipped:

```
Would import skill "graphify"
Warning: skipping skill "graphify" from /Users/michaelhamanaka/.claude/skills: already imported from an earlier source
```

That order is not alphabetical and it is not a priority list you can override.
It is fixed, and it means `~/.agents/skills` is the directory to put a skill
in when you want it to be the one that lands.

A directory that does not exist is not an error:

```
No skills directory found at /Users/michaelhamanaka/.config/opencode/skills; nothing to import.
```

Non-directory entries are skipped with a reason:

```
Skipping ".DS_Store": not a directory
```

### `sbx skills update` and `sbx skills rm`

```bash
sbx skills update
sbx skills rm graphify --force
```

`sbx skills update` refreshes **only** skills that were installed with
`sbx skills add`. A skill that was imported from the host is refreshed by
**importing it again** — `sbx skills update` will not touch it, because it has
no upstream to pull from.

`sbx skills rm` removes a stored skill. It prompts unless you pass `--force`.

`sbx skills ls` lists what is in the store.

## 2. The skills mount mode

The store is mounted **read-only by default**. The mode is chosen at creation
and does not change afterwards.

```bash
sbx run claude . --skills=off
sbx run claude . --skills=readonly
sbx run claude . --skills=readwrite
```

| Mode | What the agent sees |
| --- | --- |
| `off` | No store at all. |
| `readonly` | The store, mounted read-only. The default. |
| `readwrite` | The store, mounted read-write. The agent can change it. |

The store is mounted at the agent's skills directory — `~/.claude/skills` for
Claude, and the equivalent for other agents.

`--skills` can only be used when creating a new sandbox. It is ignored on a
re-attach.

### `skills.defaultMode`

```bash
sbx settings set skills.defaultMode readonly
sbx settings ls | grep -A1 skills.defaultMode
```

| Key | Type | Default | Note |
| --- | --- | --- | --- |
| `skills.defaultMode` | string | `readonly` | `readonly`, `readwrite`, `off`. Applied **only at creation**. |

So `--skills` on the command line overrides `skills.defaultMode`, and the
setting is the default for sandboxes that do not say.

### The trust boundary warning

**Mounting the store `readwrite` puts every participating sandbox in one
trust boundary** (ledger §10).

That is the whole point of the warning, and it is worth spelling out what it
means:

- The store is **shared state across sandboxes**. A sandbox mounted
  `readwrite` can change the skills that every other sandbox — including
  sandboxes running against repositories you care about — will load.
- A skill is **instructions the agent follows**. Changing a skill is not
  editing a data file; it is changing what the agent is told to do.
- The store is mounted read-only **by default** for exactly this reason.

Therefore:

- For an unattended test run, use `--skills=off`. The `sbx-runner`
  connector defaults to `--skills off` for exactly this reason.
- For a long-lived workshop sandbox where you want to iterate on a skill
  during the session, `readwrite` is reasonable — but you are then trusting
  every agent that can reach that sandbox.
- If you do not know why a sandbox needs `readwrite`, it does not.

## 3. Workspace modes

Four ways the workspace reaches the sandbox, with very different
consequences.

| | Direct | Clone (`--clone`) | Host worktree | Mountless |
| --- | --- | --- | --- | --- |
| Branch management | You, on the host | The agent, in the clone | You, on the host | — |
| Changes visible on the host | Immediately | After a fetch or a push | Immediately | Never |
| The agent can use Git | Yes | Yes | **No** | Yes, on nothing |
| Parallelism | No | Several agents in one sandbox | One sandbox per task | Yes |
| Fixed at creation | No | **Yes** | — | No |
| The agent can modify your files | **Yes** | No | **Yes** | No, there are none |

(The first five rows are ledger §11. The mountless row is described in §3.4
below.)

### 3.1 Direct

The workspace is bind-mounted into the sandbox, and the agent works on your
files directly.

```bash
sbx run claude /path/to/project
```

What you get:

- Changes appear on the host immediately. No fetch, no push, no merge step.
- The agent can use Git: branch, commit, push, from inside the sandbox.
- You keep branch management. The agent operates on the branch you set up.

What it costs:

- **The agent can modify your working tree.** A test that runs a formatter, a
  linter with `--fix`, or a codemod rewrites your files. A `git clean -fdx`
  in the sandbox deletes your untracked files.
- **No parallelism.** Two direct-mode sandboxes on one checkout fight over
  the index and the working tree. One sandbox per task.
- **Filesystem access policies do not protect you.** Ledger §11: direct-mode
  hard links reach outside the workspace, and filesystem access policies do
  not block that.
- The mode is **not fixed at creation**, so it is the one you can change your
  mind about — by creating a different sandbox.

Direct mode is right when the point is to get the agent's edits into your
working tree, and you are watching.

### 3.2 Clone

`--clone` gives the agent a private in-container clone of your repository.

```bash
sbx run claude . --clone
```

What you get:

- The agent branches, commits and pushes from inside the sandbox. The
  `sandbox-<name>` remote is served by a git-daemon inside the sandbox, bound
  to localhost on the host.
- Several agents can work in one sandbox.
- The source is mounted **read-only** at `/run/sandbox/source`, so a write
  attempt fails at the filesystem level.
- The mode is **fixed at creation**.

What it costs:

- **Changes reach the host only after a fetch or a push.** You are now
  merging like any other reviewer.
- **Clone mode protects against modification, not inspection.** The
  read-only source mount covers the whole working directory **including
  untracked and ignored files**. A `.env` in your checkout is readable from
  the sandbox. Read-only is not invisible.
- **Local-path remotes are not copied into the clone.** If your repository's
  `origin` is a `file://` URL or a filesystem path, the clone will not have
  it. A clone that has no reachable origin is a clone that cannot push.
- **Clone mode is rejected from inside a Git worktree other than the main
  one.** If you are in a linked worktree when you run `sbx run --clone`, the
  command fails.

The git-daemon's port is ephemeral. `sbx stop` stops it; a restart assigns a
**new** ephemeral port and the CLI rewrites the remote URL; `sbx rm` removes
the remote entry. So a script that hard-codes the remote URL breaks after a
restart. Read the URL from the sandbox, or push by branch name.

### 3.3 Host worktree

A host Git worktree mounted as the workspace.

What you get:

- Changes appear on the host immediately, the same as direct mode.
- You keep branch management, and the host worktree gives you a clean
  per-task branch.

What it costs:

- **The agent cannot use Git at all.** A worktree's `.git` is a *file* that
  points at the main repository's `.git/worktrees/…` directory. The sandbox
  cannot resolve that pointer, so `git` commands in the sandbox fail. The
  agent can still edit files, and you can still commit on the host.

Host worktree mode is for "one sandbox, one task, one branch, and the agent
edits files but does not need to commit".

> The ledger records the host-worktree **consequences**; it does not record
> the exact command or flag that creates one. **Not verified in the ledger.**
> Create the worktree on the host with `git worktree add` and mount it the
> way you would mount any directory, then confirm the agent's Git access
> before relying on it.

### 3.4 Mountless

No workspace at all. The agent works in the container's own filesystem.

```bash
sbx create shell
sbx run shell
```

`sbx create` with no path creates a sandbox without a workspace bind mount:
the agent then works in the container's own filesystem instead of on your
files.

Use it for:

- A sandbox whose only job is to run a command — a test suite that fetches
  its own source, a build check, a formatter over a tarball.
- A sandbox that must not see your files at all.
- The `mountless` mode of the `sbx-runner` connector.

The cost: there is nothing on the host to see the results, and nothing for
the agent to work on. It is a clean room, not a workspace.

## 4. Choosing

| Situation | Mode |
| --- | --- |
| An agent should edit your working tree and you are watching | Direct |
| An agent should work on a branch and you will review the diff | Clone |
| One agent, one task, one branch, and the agent should not commit | Host worktree |
| A test run that must not touch your files | Clone, or mountless |
| Two agents in parallel on the same project | Clone (several agents in one sandbox), or one clone per agent |
| An unattended test run | Clone, with `--skills off` |

The safe default for anything unattended is **clone**, and it is the default
in `connectors/sbx-runner/sbx_runner.py` for exactly that reason.

## 5. Putting them together

A sandbox with a private clone and no shared skills:

```bash
sbx run claude . --clone --skills=off
```

A sandbox with the skills store available read-only:

```bash
sbx run claude . --clone --skills=readonly
```

An interactive sandbox where you are editing a skill while the agent works:

```bash
sbx run claude . --clone --skills=readwrite
```

And the check that the mode is what you think it is:

```bash
sbx inspect my-sandbox --json
```

`sbx ls --json` reports each sandbox's mode, and from v0.48.0 the rows carry
`created_at`, `last_used_at` and `detached` as well. See
`03-structured-output-and-parsers.md` for the shape difference between
versions.

## What this does not cover

- **What a `SKILL.md` should contain.** The store is a directory of them; the
  format is not described in the ledger.
- **How the store is mounted into each agent.** The mount point is the
  agent's skills directory (`~/.claude/skills` and equivalents); the
  mechanism is not in the ledger.
- **Whether a skill in the store can be overridden per sandbox.** The ledger
  does not record an override mechanism.
- **The exact command for host worktree mode.** The consequences are in
  ledger §11; the spelling is **not verified in the ledger**.
- **The mountless mode's interaction with kits.** A kit that installs into
  `/home/agent` works in every mode; a kit that expects a workspace does not.
  The ledger does not record a per-mode kit contract.
- **The cloud equivalents.** Cloud sandboxes have no host workspace at all.
  Every mode in this guide is local.
