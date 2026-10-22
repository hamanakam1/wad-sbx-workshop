# Kits and the supply chain

## What this is for

A kit is a declarative YAML artifact that describes what a sandbox needs before
an agent touches it: extra network permissions, environment variables, install
commands, files to write. A kit is also a supply-chain artifact — it can be
packed, pushed, signed, pulled and verified.

This guide covers the v2 kit format in depth (the format the built-in agents
use), the argument pattern rule and why it exists, the execution order of
setup steps, the chown rule, idempotence, what a signature does and does not
cover, the trust settings, and the `kit.allowedSources` gate this workshop
actually hits.

Then it walks through this workshop's own kit,
`connectors/kits/dashboard-tests/spec.yaml`, line by line.

## Before you start

- The `sbx` CLI on your `PATH` and a Docker login.
- A v2 kit to read. The smallest complete examples are in `factory/`:
  `factory/hello-kit/spec.yaml` (install step only) and
  `factory/browser-access/spec.yaml` (network permissions only).
- Read [`00-verified-facts.md`](00-verified-facts.md) §6 and §7. This guide is
  the applied version of those two sections.
- The pinned ACR kit source, which is the thing that will be refused:
  `factory/sbxenv.yaml` and `factory/README.md`.

```bash
sbx version --json
sbx kit --help
```

## 1. v2 versus v3

| | v2 | v3 |
| --- | --- | --- |
| Descriptor | `spec.yaml`, `schemaVersion: "2"` | YAML with `# syntax=docker/sandbox-kit:3` |
| Kinds | `sandbox`, `mixin` | `workload`, `mixin`, plus kit sets |
| Composition | `extends:`, `mixins:` | `requires:`, `provides:`, `integrates:`, `conflicts:` |
| Build | `sandbox.build` (runtime pending) | Dockerfile recipe, built with **Buildx** |
| Minimum sbx | v0.36 | v0.45 |
| Published as | ZIP or OCI | OCI image |
| Signable | local dir, Git, OCI (a **ZIP cannot carry a verifiable signature**) | **OCI image only** |

Three consequences that catch people out:

- **v3 kits cannot compose with v1 or v2.**
- **The built-in agent names (`claude`, `codex`, …) still select v2 kits.** So
  `sbx run claude --kit ./some-v3-mixin` fails. You cannot mix a v3 mixin
  into a built-in agent.
- **`sbx kit pack` / `push` / `pull` are v1/v2 tooling.** v3 kits are published
  with `docker buildx build`.

Everything in this guide is v2, because v2 is what the built-in agents use and
what this workshop ships.

### Capability groups (v3, v0.48.0+)

A group is selected only if the runtime can provide **every** capability in it.

- An unavailable capability in an **optional** group skips the group **in
  full** — including its network rules, setup commands, generated files and
  agent instructions. Not partially: the whole group goes.
- An unavailable capability in a **required** group fails sandbox creation.

Named capabilities:

| Capability | Since | Needs |
| --- | --- | --- |
| `com.docker.sandbox/long-running@1` | v0.47.0 | Nothing. It is **config-less**: it must not carry a `config` key, not even empty or null. Cloud sandboxes cannot provide it. |
| `com.docker.sandbox/ssh-agent@1` | v0.48.0 | SSH forwarding enabled and a host agent. |
| `com.docker.sandbox/git-identity@1` | v0.48.0 | `user.name` and `user.email` in the **host** Git config. |

## 2. The v2 `spec.yaml` anatomy

Every block, and what it does. `factory/hello-kit/spec.yaml` and
`factory/browser-access/spec.yaml` are the minimal examples; the full one is
`connectors/kits/dashboard-tests/spec.yaml`.

| Block | What it does |
| --- | --- |
| `schemaVersion: "2"` | Declares the format. Must be the string `"2"`, not the number 2. |
| `kind: mixin` | `mixin` extends an agent; `sandbox` is a whole agent. |
| `name:` | The kit's identifier. |
| `description:` / `displayName:` | Prose. `displayName` is the human-facing title. |
| `version:` | The kit's version string. |
| `args:` | Declared arguments: a map of name → `{default, description, pattern, required}`. |
| `permissions:` → `network:` → `allow:` | The destinations the kit's traffic needs. |
| `environment:` → `variables:` | Environment variables baked into the sandbox. |
| `setup:` → `install:` | Shell commands run at creation. |
| `setup:` → `files:` | Files written into the sandbox, with `path`, `mode` and `content`. |

### `args`

```yaml
args:
  python_version:
    default: "3.12"
    description: "Python minor version to install from the distribution repositories."
    pattern: '^[0-9]{1,2}\.[0-9]{1,2}$'
  test_command:
    default: "python3 -m pytest -q"
    description: "The command the runner executes when it is given no arguments."
    pattern: '^[A-Za-z0-9 _./,:=-]+$'
```

An argument can be `required: true` (as `factory/sbxenv.yaml` does for `name`)
or carry a `default`. Values are supplied at creation time:

```bash
sbx run claude . --kit ./connectors/kits/dashboard-tests \
  --kit-arg python_version=3.12 \
  --kit-arg test_command="python3 -m pytest -q"
```

`--kit-arg` takes `name=value` for every kit, or `kit.name=value` for one
kit. `--kit-args-file` takes a file of `name=value` lines, one per line, and
`--kit-arg` overrides it.

### `permissions.network.allow`

```yaml
permissions:
  network:
    allow:
      - "pypi.org"
      - "files.pythonhosted.org"
```

**Pattern enforcement in v2** (ledger §6):

| Pattern | Status |
| --- | --- |
| An exact host, `host:port`, `*.example.com` | **Enforced** |
| `**.example.com`, port ranges, a port wildcard, CIDR | Parsed, **enforcement pending** |

So `*.example.com` works and `**.example.com` does not, even though both
parse. Do not build a kit around the pending forms.

Deny always beats allow, across every layer and every composed kit. Under
**organisation governance**, only organisation allow rules grant access: local
allow rules and **kit-defined allow rules are inactive**, and deny rules from
every source still apply.

### `environment.variables`

```yaml
environment:
  variables:
    PYTHONDONTWRITEBYTECODE: "1"
    PYTHONUNBUFFERED: "1"
    PIP_DISABLE_PIP_VERSION_CHECK: "1"
    PIP_NO_INPUT: "1"
    SBX_TEST_COMMAND: "${{ kit.args.test_command }}"
```

Note the last line: an argument value can be referenced from the environment
block. That reference is a raw text splice — see §3.

### `setup.install` and `setup.files`

```yaml
setup:
  install:
    - user: root
      description: "Install Python, pip and the CA bundle, then a virtualenv for the suite."
      command: |
        set -eu
        ...
  files:
    - path: /home/agent/run-tests.sh
      mode: "0755"
      content: |
        #!/bin/sh
        ...
```

An install step has a `user`, a `description` and a `command`. A file has a
`path` (absolute), a `mode` and `content`.

## 3. The argument pattern rule, and why it matters

### The rule

Every argument that gets spliced into a command or a file must declare a
`pattern` that is a valid regular expression, and the pattern must exclude
shell metacharacters.

The runtime validates it: an invalid pattern is a validation error, not a
warning.

### Why it matters: raw text splicing

An argument value is **spliced as raw text** into the kit's YAML **before the
YAML is parsed**. The value is not quoted, escaped or validated at splice
time. Whatever characters the pattern permits arrive verbatim, in whatever
position the reference put them.

The ledger does not record this mechanism. `FIELD-GUIDE.md` in this
repository states it, and marks it verified: "args are string inputs, spliced
into the YAML as raw text *before* it is parsed. The `pattern` is what stops
a value from breaking out of a shell string. Never pass secrets as args."

In `dashboard-tests`, `"${{ kit.args.python_version }}"` appears inside a
shell command:

```yaml
command: |
  apt-get install -y --no-install-recommends \
    ca-certificates \
    "python${{ kit.args.python_version }}" \
    "python${{ kit.args.python_version }}-venv" \
    python3-pip
```

and `"${{ kit.args.test_command }}"` appears inside a YAML double-quoted
string that becomes a shell variable:

```yaml
SBX_TEST_COMMAND: "${{ kit.args.test_command }}"
```

The pattern is the only thing standing between an argument value and the
shell.

### What each pattern buys

| Argument | Pattern | Permits | Refuses |
| --- | --- | --- | --- |
| `python_version` | `^[0-9]{1,2}\.[0-9]{1,2}$` | `3.12`, `3.11`, `3.9` | anything with a letter, a space, a shell metacharacter, or a second dot |
| `test_command` | `^[A-Za-z0-9 _./,:=-]+$` | `python3 -m pytest -q`, `npm test`, `go test ./...` | `;` `&` `|` `$` backtick `(` `)` `*` `?` `>` `<` newline |

`python_version` is spliced inside double quotes in the shell, so a space
would break the word into two. The pattern forbids it. `test_command` is
spliced into a YAML double-quoted string, so a `"` or a `\` would change the
structure of the generated file. The pattern forbids it.

### The second layer: the consumer

A pattern is necessary but not sufficient. The consumer must also not
re-interpret the value. `dashboard-tests` does that with `set -f`:

```sh
set -f
if [ "$#" -gt 0 ]; then
  set -- "$@"
else
  # shellcheck disable=SC2086
  set -- $SBX_TEST_COMMAND
fi
```

- `set -f` disables pathname expansion, so a `*` in the value does not glob.
- The expansion is unquoted, so it is **word-split** into an argument list.
  The shell does not re-parse the expansion, so a `;` in the value would
  arrive as a literal argument, not as a command separator. The pattern
  stops that argument from ever existing.
- `set -- "$@"` in the `if` branch preserves the arguments the script was
  actually given, quoted, with no splitting at all.

So the value is word-split once, by the shell, and never re-interpreted. The
pattern defines what that split means; the `set -f` guarantees the split
cannot expand into a glob.

### What a bad pattern looks like

```yaml
# Wrong: the value can carry "; rm -rf /" and it is spliced raw.
test_command:
  default: "pytest"
  pattern: '.+'
```

```yaml
# Right: the character class is the contract.
test_command:
  default: "python3 -m pytest -q"
  pattern: '^[A-Za-z0-9 _./,:=-]+$'
```

**Never pass a secret as an argument.** An argument value is visible in the
kit's effective spec, in `sbx kit inspect --json`, and in anything that
dumps the resolved kit. Use a secret from the secret store instead.

### Validation is not enforcement

`sbx kit validate` checks **structure only**. It does not run the install
commands, it does not test that the allowed hosts are reachable, and it does
not catch conflicts between kits.

That includes the network patterns: a `**.example.com` rule **validates
cleanly** even though the ledger records multi-label wildcards as
"parsed, enforcement pending". A kit that passes `sbx kit validate` can
still fail at run time.

The ledger's v2 pattern table (ledger §6) is the enforcement table:

| Pattern | Status |
| --- | --- |
| An exact host, `host:port`, `*.example.com` | **Enforced** |
| `**.example.com`, port ranges, a port wildcard, CIDR | Parsed, **enforcement pending** |

## 4. The execution order of setup steps

1. **`setup.install` steps run first, in the order they are listed.** They run
   through `sh -c`.
2. **`setup.files` are written after the install steps**, as the agent user.
3. **Install steps re-run on every sandbox creation.** They are not a
   one-time bake into a published image.

(The ordering is confirmed by the runtime's own message text — "install step
(which runs first) already wrote it" — and is the layout every kit in this
repository uses. The ledger does not record an explicit ordering guarantee, so
treat it as the documented layout rather than a promise. `FIELD-GUIDE.md`
records the same order and marks it verified.)

The practical consequence: **an install step can rely on a file it writes, and
a file can rely on a tool an install step installed.** The `dashboard-tests`
file script calls `/home/agent/.venv/bin/python`, which the install step
creates.

The second consequence is the one that bites: because install re-runs on every
recreate, an install step that is not idempotent will do work again every
time, and may leave the sandbox in a different state than it found it.

## 5. The chown rule

**The rule: an install step that runs as root must chown exactly the paths it
created, and nothing else.**

Why it exists. The agent runs as a non-root `agent` user, UID 1000, with
passwordless sudo inside the VM (ledger §10). The workspace belongs to that
user. If a root-run install step creates a directory under `/home/agent` and
leaves it root-owned, the agent cannot write there and the failure surfaces
much later as a permission error in a test, with no obvious cause.

The `dashboard-tests` install step does it correctly, and says so in a
comment:

```sh
if [ ! -x /home/agent/.venv/bin/python ]; then
  rm -rf /home/agent/.venv
  python3 -m venv /home/agent/.venv
  # Only the directory this step created is chowned. Nothing else
  # under /home/agent is touched.
  chown -R agent:agent /home/agent/.venv
fi
```

`chown -R agent:agent /home/agent` would be wrong. It would take ownership of
the agent's dotfiles, its caches, its shell configuration, and anything else
that happens to live there — including files the kit knows nothing about.
A kit that changes ownership of files it did not create is a kit that can
break an agent in ways that look like agent bugs.

The other half of the rule: **do not chown the whole workspace.** The
workspace is already the agent's. Only the paths your step created.

Which account a step runs as is chosen with the `user:` key.

**Install steps run as root by default.** The ledger does not record this;
`FIELD-GUIDE.md` in this repository does, and marks it verified: "`setup.install`
runs through `sh -c`, **as root unless you say `user: agent`**, at creation
and again on every recreate."

So a step that means to run as the agent must say `user: agent` explicitly.
`factory/hello-kit/spec.yaml` does; `connectors/kits/dashboard-tests/spec.yaml`
says `user: root` because it installs system packages.

**Files are written as the agent user, not as root.** `FIELD-GUIDE.md` records
that `setup.files` are written at start as the agent user, and that only
`${WORKDIR}` is substituted in their content. That is why the chown rule
applies to install steps only: a file you declare is already owned by the
agent.

Two more facts from the same source, worth knowing:

- `schemaVersion: "2"` selects a **strict grammar**: any unknown field is an
  error. A typo like `netwrok:` fails validation instead of silently doing
  nothing.
- If two kits set the same `environment.variables` key, **the last kit
  silently wins**.

> The v3 kit schema has a `chown_to_run_user` field. It is **not verified in
> the ledger** — it was observed in the v0.45.0-rc2 binary only — and it is
> not part of the v2 format this guide covers.

## 6. Idempotence

Because install steps re-run on every recreate, they must be idempotent.

Three ways to be idempotent, and the kit uses all three:

| Technique | Where |
| --- | --- |
| A sentinel path as the guard | `if [ ! -x /home/agent/.venv/bin/python ]; then … fi` |
| Clean up before you rebuild | `rm -rf /home/agent/.venv` inside the guard, so a partial venv does not survive |
| Make the command itself repeatable | `apt-get install -y` is repeatable; `apt-get install -y` without a guard would re-resolve every time, which is why the venv is guarded and the packages are not |

The file the kit writes says it too: "Safe to re-run."

And note what idempotence is *not*: it is not "the step is skipped". A
package install still runs on every recreate. Idempotence means the second run
leaves the sandbox in the same state as the first, not that the second run
does nothing.

## 7. The supply chain commands

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

| Command | What it does |
| --- | --- |
| `pack` | Package a directory as a kit artifact (a ZIP). |
| `push` | Push a kit to an OCI registry. `--sign` signs the manifest and its provenance together. |
| `pull` | Pull a kit artifact from an OCI registry. From v0.47.0, pulls enforce blob, decompressed-size and entry-count limits. |
| `sign` | Sign a kit artifact. Keyless by default; `--key` for a cosign key. |
| `verify` | Verify a signature, by keyless identity or by key. |
| `provenance` | Show the SLSA provenance attached to a kit. |
| `validate` / `inspect` | Check an artifact without deploying it. From v0.49.1, `--offline` works without the network. |
| `add` | Add a mixin to a running sandbox. From v0.48.0, kits can carry startup commands. |

Two v0.47.0 fixes worth knowing: `sbx kit sign` and `sbx kit push --sign`
work on registries that refuse manifest deletion (GHCR, Docker Hub). Before
that they failed on exactly the registries most people publish to.

## 8. What a signature covers, and what it does not

**Covers:** `spec.yaml` and `files/`.

**Does not cover:**

- **Image tags.** A signature does not pin a tag to a digest.
- **Bytes that an install command downloads.** This is the important one. A
  kit whose install step runs `curl … | sh` is signed; the thing it downloads
  at run time is not. Ledger §17 records this as by design, and the fix is to
  pin those bytes by digest.

The second point is a supply-chain hole with a known answer, not an oversight.
If your kit downloads anything, download a digest-pinned artifact and verify
the digest in the install command.

**Provenance is unsigned by default.** `sbx kit push --sign` signs the
manifest and its provenance together. Without `--sign`, the provenance
attribute exists but carries no signature.

Sigstore and cosign compatible, keyless by default.

A **ZIP kit cannot carry a verifiable signature** (ledger §6). This is why
`kit.requireSignature` rejects ZIP kits outright.

## 9. The trust settings

```
sbx settings set kit.allowedSources '["docker.io/","github.com/myorg/"]'
sbx settings set kit.allowLocalKits false
sbx settings set kit.allowExtractedAgents false
sbx settings set kit.requireSignature true
sbx settings set kit.trustedSigners '[{"identityRegexp":"^.*@docker\\.com$","issuer":"https://accounts.google.com"}]'
sbx settings set kit.ignoreTransparencyLog true
```

| Setting | Default | What it does |
| --- | --- | --- |
| `kit.allowedSources` | `["docker.io/"]` | Which registry and repository paths a kit may be pulled from. |
| `kit.allowLocalKits` | `true` | Whether local directories and v2 ZIP files may be used. |
| `kit.allowExtractedAgents` | `true` | Exempts Docker's pinned agent references from the allowlist and from the signature requirement. |
| `kit.requireSignature` | `false` | Rejects unsigned kits, kits whose signature does not match, and **ZIP kits**. |
| `kit.trustedSigners` | Docker employee identities | A signature matching any entry is accepted. |
| `kit.ignoreTransparencyLog` | `false` | For kits signed `--tlog-upload=false`; needs a TSA timestamp. |

Three details that matter:

- **`kit.allowedSources` replaces the whole list.** It is not a merge. Setting
  it to `["github.com/myorg/"]` removes `docker.io/` from the list.
- **Prefix matching is on path-segment boundaries.** `github.com/myorg/`
  allows that organisation and **not** `github.com/myorg-other/`.
- **Setting `kit.trustedSigners` alone does not require signatures.** You also
  need `kit.requireSignature true`.

Settings precedence: environment variable, then `sbx settings set` override,
then the built-in default. Most changes take effect in about five seconds;
`RESTART` keys need `sbx daemon restart`.

From v0.49.1, `kit.allowedSources` and `kit.trustedSigners` are **structure
checked before saving**, so a malformed value is rejected at the point you set
it rather than at the point a kit is pulled.

## 10. The `kit.allowedSources` gate this workshop hits

This is not a hypothetical. `factory/sbxenv.yaml` declares five kits:

```yaml
kits:
  - source: ./browser-access
  - source: ./hello-kit
  - source: "git+https://github.com/shelajev/acr-sbx-kit.git#ref=f446b95bccabd879912db174c190ff09377b7c7b"
  - source: ../chapters/kits/pi
  - source: ../chapters/kits/herdr
```

Four of those are local directories. Local directories are covered by
`kit.allowLocalKits`, which defaults to `true`.

The fifth is a `git+https://` source. That is **not** in the default
`kit.allowedSources` of `["docker.io/"]`, and a fresh-environment proof in
`factory/README.md` reached exactly that gate:

> A fresh-environment proof with an isolated app clone and port3103 reached
> the native `kit.allowedSources` policy gate for the pinned ACR Git source.
> The current host setting allows only docker.io/. No new sandbox or
> registration was created and no global source policy was changed.

Two things follow from that sentence, and they are the whole lesson:

1. **The failure is a policy gate, not a network error.** The source is
   refused because of where it comes from, not because it could not be
   fetched. `sbx plan` on the environment, or the creation itself, reports the
   source as disallowed.
2. **Authorising a source is a separate decision from approving a plan.**
   `factory/README.md` says it plainly: "reviewed per-repository source
   authorization is separate from ordinary plan approval." Approving the
   environment plan does not authorise the source.

### What to do about it

The correct move is a **per-repository source authorisation**, scoped to the
one source you have reviewed:

```bash
# Read the current list first. `sbx settings set` REPLACES it.
sbx settings ls | grep -A1 kit.allowedSources

# Add the reviewed source, keeping docker.io/ in the list.
sbx settings set kit.allowedSources '["docker.io/","github.com/shelajev/"]'
```

Because the list is replaced wholesale, read it, merge, and set it in one
step. Do not set it to `["github.com/shelajev/"]` and assume Docker Hub is
still allowed.

`github.com/shelajev/` is the prefix that authorises that organisation. It
does not authorise `github.com/shelajev-other/`, because matching is on
path-segment boundaries.

The pinned ref in `factory/sbxenv.yaml` is
`#ref=f446b95bccabd879912db174c190ff09377b7c7b`. Pinning the ref is what
makes the authorisation reviewable: the source you approved is that commit.

### What not to do

| Tempting | Why not |
| --- | --- |
| `sbx settings set kit.allowedSources '["*"]'` | Defeats the gate. Prefix matching is on path-segment boundaries, so a bare `*` is a much wider grant than it looks. |
| `kit.allowLocalKits false` "to fix it" | That is a different gate, and it breaks the four local kits, which are the ones that work. |
| Turn off `kit.requireSignature` | The signature requirement is a separate setting. Turning it off does not widen `kit.allowedSources`, and it loses a protection. |
| Skip the source and use the ACR kit's released OCI image | Sometimes the right answer, and it needs the same allowlist entry for the registry and repository you choose. |

### The related setting

`kit.allowExtractedAgents` (default `true`) exempts **Docker's pinned agent
references** from the allowlist and the signature requirement. That exemption
is for Docker's own agent references. It is not a general exemption for
third-party kits, and it is not a way to pull the ACR kit in.

## 11. This workshop's kit: `connectors/kits/dashboard-tests/`

### What it is

A v2 mixin that provisions a Python test toolchain inside a sandbox and
writes a runner script next to the code under test.

```yaml
schemaVersion: "2"
kind: mixin
name: dashboard-tests
description: "Test tooling for running a Python test suite inside a Docker Sandbox, with a runner that writes a tree digest and JUnit XML next to the code it tested."
displayName: "Dashboard test tooling"
version: "1.0.0"
```

### The arguments, and why the patterns are what they are

```yaml
args:
  python_version:
    default: "3.12"
    description: "Python minor version to install from the distribution repositories."
    pattern: '^[0-9]{1,2}\.[0-9]{1,2}$'
  test_command:
    default: "python3 -m pytest -q"
    description: "The command the runner executes when it is given no arguments."
    pattern: '^[A-Za-z0-9 _./,:=-]+$'
```

**`python_version`** is spliced into a shell command inside double quotes:

```sh
apt-get install -y --no-install-recommends \
  ca-certificates \
  "python${{ kit.args.python_version }}" \
  "python${{ kit.args.python_version }}-venv" \
  python3-pip
```

The pattern `^[0-9]{1,2}\.[0-9]{1,2}$` permits exactly one digit pair, a
dot, and one more digit pair. That is the shape of a Debian package name
(`python3.12`, `python3.12-venv`). It permits no letter, no space, no quote,
no `;`, no `$`, no backtick and no newline — so the value cannot close the
double quotes, cannot start a command, and cannot be a second argument. A
value of `3.12" ; rm -rf / ; echo "` fails the pattern, so it never reaches
the shell.

Note the pattern is anchored at both ends. Without the `^` and `$`, the value
`3.12; rm -rf /` would contain a *matching substring* and pass. Anchoring is
what makes the pattern a description of the whole value rather than of part
of it.

**`test_command`** is spliced into a YAML double-quoted string:

```yaml
SBX_TEST_COMMAND: "${{ kit.args.test_command }}"
```

and then word-split by the generated script. The pattern
`^[A-Za-z0-9 _./,:=-]+$` permits the characters a command line needs —
letters, digits, space, underscore, dot, slash, colon, comma, equals, hyphen
— and nothing else. It refuses `"` and `\`, which would change the structure
of the generated YAML-turned-shell file. It refuses `;`, `&`, `|`, `$` and
backtick, which would put shell syntax into an argument. It refuses `*` and
`?`, which would glob — the script also sets `set -f`, so this is belt and
braces.

The default, `python3 -m pytest -q`, is inside the permitted class, so the
default passes its own pattern. A default that fails its own pattern is a
kit that cannot be used without overriding the argument.

### The network permissions

```yaml
permissions:
  network:
    allow:
      - "pypi.org"
      - "files.pythonhosted.org"
```

Two exact hosts, both in the enforced form. `pypi.org` is the index;
`files.pythonhosted.org` is where the wheels actually come from. Allowing
only the index would let `pip install` resolve and then fail to download.

The kit does not allow `*.pythonhosted.org` or any CIDR. The enforced forms
are the exact host, `host:port` and `*.example.com`; the pending forms
(`**.example.com`, port ranges, port wildcards, CIDR) parse but are not
enforced, and a kit that depends on a pending form is a kit that will
silently stop working.

### The environment

```yaml
environment:
  variables:
    PYTHONDONTWRITEBYTECODE: "1"
    PYTHONUNBUFFERED: "1"
    PIP_DISABLE_PIP_VERSION_CHECK: "1"
    PIP_NO_INPUT: "1"
    SBX_TEST_COMMAND: "${{ kit.args.test_command }}"
```

The first four make a non-interactive pip behave: no `.pyc` files written
into the tree under test (which would change the tree digest), unbuffered
output (so a hung test still shows its last line), no version-check network
call, and no interactive prompt.

`SBX_TEST_COMMAND` is the hand-off point: the argument value becomes an
environment variable, and the generated script reads it.

### The install step

```sh
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates \
  "python${{ kit.args.python_version }}" \
  "python${{ kit.args.python_version }}-venv" \
  python3-pip
rm -rf /var/lib/apt/lists/*
if [ ! -x /home/agent/.venv/bin/python ]; then
  rm -rf /home/agent/.venv
  python3 -m venv /home/agent/.venv
  chown -R agent:agent /home/agent/.venv
fi
/home/agent/.venv/bin/python -m pip install --upgrade pip
```

Point by point:

- `set -eu` — fail on the first error, and fail on an unset variable.
- `DEBIAN_FRONTEND=noninteractive` — no debconf prompt, which would hang an
  unattended create.
- `--no-install-recommends` — a smaller image and fewer surprises.
- `ca-certificates` in the same install — the bundle that the eMudhra rebuild
  in v0.49.2 fixes. Having it in the kit means a fresh sandbox has a current
  CA store even if the template image is old.
- `rm -rf /var/lib/apt/lists/*` — the apt lists are large and are not needed
  after the install.
- The venv is at `/home/agent/.venv`, and the suite runs from it, so the
  sandbox's system Python is never modified by a `pip install`.
- The guard is the sentinel path `/home/agent/.venv/bin/python`, and the
  `rm -rf` inside the guard means a half-built venv cannot survive a failed
  create. The step re-runs on every recreate.
- `chown -R agent:agent /home/agent/.venv` — exactly the directory the step
  created, and nothing else under `/home/agent`.

### The generated file

`/home/agent/run-tests.sh`, mode `0755`. It does four things.

**It digests the tree under test**, so a receipt can name exactly what was
executed:

```sh
find "$WORKSPACE" -path "$WORKSPACE/.git" -prune -o -type f -print 2>/dev/null \
  | sed "s|^$WORKSPACE/||" | LC_ALL=C sort > "$RESULTS/files.txt" || true
: > "$RESULTS/tree.sha256"
while IFS= read -r path; do
  [ -n "$path" ] || continue
  sum=$(sha256sum "$WORKSPACE/$path" 2>/dev/null | cut -d" " -f1) || continue
  [ -n "$sum" ] || continue
  printf '%s  %s\n' "$sum" "$path" >> "$RESULTS/tree.sha256"
done < "$RESULTS/files.txt"
```

Sorted relative paths, `.git` excluded, one line per file. `LC_ALL=C sort` so
the ordering does not depend on the host locale.

**It installs the declared dependencies** when the project has them:

```sh
if [ -f "$WORKSPACE/requirements.txt" ]; then
  /home/agent/.venv/bin/python -m pip install -q -r "$WORKSPACE/requirements.txt" || \
    echo "deps        pip install failed; continuing with what is installed"
fi
```

Note the `|| echo`. A dependency install failure does not abort the run; it
is reported and the suite runs with whatever is installed. Whether that is
right is a decision for whoever consumes the result — a test suite that
imports a missing module will fail anyway, and a suite that does not import
it should still run.

**It picks the command**, as described in §3: the arguments the script was
given, or `$SBX_TEST_COMMAND` word-split.

**It adds JUnit XML when the command is a pytest run:**

```sh
case "$*" in
  *pytest*) set -- "$@" "--junitxml=$RESULTS/junit.xml" ;;
  *) : ;;
esac
```

The `case` matches on `$*`, so the decision is made on the command line, not
on the program name. A pytest run gets
`--junitxml=$RESULTS/junit.xml` appended; anything else does not. The
pattern on `test_command` guarantees the appended argument cannot be
confused with part of the command.

**It runs the suite and propagates the status:**

```sh
set +e
( cd "$WORKSPACE" && "$@" )
status=$?
set -e
```

`set +e` around the run so a non-zero exit does not abort the script under
`set -e`; `set -e` restored immediately after. The status is captured and
re-raised at the end:

```sh
echo "exit_code   $status"
echo "results     $RESULTS"
exit "$status"
```

The suite runs in a subshell with `cd "$WORKSPACE"`, so the `cd` does not
change the script's own directory.

### Using it

```bash
sbx run claude . \
  --kit ./connectors/kits/dashboard-tests \
  --kit-arg python_version=3.12
```

Or, for the non-agent case:

```bash
sbx create --name testbed --clone shell .
sbx exec testbed -- sh /home/agent/run-tests.sh
```

The results land in `$WORKSPACE/.sbx-test-results/`:

```
files.txt        the sorted relative path list
tree.sha256      one line per file: digest, two spaces, path
junit.xml        the JUnit report, when the command was a pytest run
```

Copy them out with `sbx cp` — see `02-automated-sandbox-testing.md` for the
mechanics and the destination-directory gotcha.

### Checking it without deploying it

```bash
sbx kit validate ./connectors/kits/dashboard-tests --json
sbx kit inspect ./connectors/kits/dashboard-tests --json
```

From v0.49.1, add `--offline` to validate without network access.

## What this does not cover

- **v3 kits in detail.** The v3 format uses a Dockerfile recipe, Buildx, and
  `requires:` / `provides:` / `integrates:` / `conflicts:`. This guide
  describes v2, which is what the built-in agents and this workshop use.
- **The `sandbox.build` block.** The v2 build block is "runtime pending"
  (ledger §6). This guide covers the `setup:` blocks, which do work.
- **MCP Cedar policies inside a kit.** Ledger §5 describes the MCP policy
  layer, but the kit-side authoring surface is not covered here.
- **What a specific registry requires.** `sbx kit push` to GHCR, Docker Hub
  and a private registry have different authentication and different
  manifest-deletion behaviour. The ledger only records the v0.47.0 fix for
  registries that refuse manifest deletion.
- **Key management.** `sbx kit sign --key cosign.key` names a key file. Where
  it comes from, how it is protected, and how it is rotated are operational
  questions this guide does not answer.
- **The `chown_to_run_user` field.** Observed in the v0.45.0-rc2 binary's v3
  schema. **Not verified in the ledger**, and not part of v2.
- **The exact default account for an install step that omits `user:`.** The
  ledger does not record it. Every kit in this repository sets `user:`
  explicitly.
