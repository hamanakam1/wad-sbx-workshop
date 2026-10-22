# MCP connectors

## What this is for

An agent inside a Docker Sandbox cannot reach your laptop. It has no host
filesystem, no route to your binaries, and no path to the host Docker daemon.
The escape hatch is the MCP gateway: you register an MCP server on the host,
the sandbox's gateway brokers the connection, and the agent gets named tools
instead of a mount.

This guide covers the `sbx mcp` surface — how to register a server, how to
authorise it, how to attach it to a running sandbox — and then the connector
this workshop ships, `connectors/sbx-mcp/sbx_mcp.py`, which exposes Docker
Sandboxes themselves to an agent with a hard read/write separation.

## Before you start

- The `sbx` CLI on your `PATH` and a Docker login: `sbx login`.
- The name of the MCP server you want to register, and how it is reached:
  a remote URL, or a command to run on the host.
- For a remote server that needs OAuth: a browser, at least once.
- Read [`00-verified-facts.md`](00-verified-facts.md) §3 (the `--skip_auth` →
  `--skip-auth` rename) and §5 (MCP policy).

```bash
sbx version --json
sbx mcp ls
```

## 1. The `sbx mcp` surface

```
sbx mcp add        Register an MCP server
sbx mcp auth       Authorize MCP servers
sbx mcp catalog    List the servers in Docker's hosted MCP catalog
sbx mcp inspect    Show MCP server details
sbx mcp load       Load an already-registered MCP server into a running sandbox
sbx mcp ls         List MCP servers, grouped by the gateway that serves them
sbx mcp rm         Remove a registered MCP server
```

`catalog`, `auth` and `load` exist in v0.45.0-rc2 (ledger §2).
**`sbx mcp unload` is new in v0.48.0** (ledger §1) and is not in the local
binary.

### Registering with `sbx mcp add`

```
sbx mcp add <name> (--url <url> | --command <cmd>) [flags]
```

| Flag | Applies to | What it does |
| --- | --- | --- |
| `--url <url>` | remote | The server to register. Four formats are auto-detected. |
| `--local` | `--url` | Run a registry OCI server locally via `docker run` (stdio packages only). |
| `--command <cmd>` | local stdio | The executable to run for a local stdio server. **Runs on the host.** |
| `--args <args>` | `--command` | Command-line arguments, comma-separated. |
| `--dir <dir>` | `--command` | Working directory (cwd) for the host process. |
| `--header <hdr>` | `--url` | A custom HTTP header, curl form `Name: value`. Repeatable. |
| `--scope <scope>` | `--url` | Default OAuth scope to request at consent time. Repeatable. |
| `--no-scope` | `--url` | Request no scopes, so the server applies its own default grant. Cannot be combined with `--scope`. |
| `--client-id <id>` | `--url` | OAuth client id for a pre-registered client. |
| `--oauth-authorization-server <p>` | `--url` | Path or http(s) URL to an RFC 8414 authorization-server metadata JSON document. |
| `--resource <uri>` | `--url` | RFC 8707 resource indicator. An absolute URI with a scheme and a host, no fragment. |
| `--skip-auth` | `--url` | Register an OAuth server without starting the hosted OAuth flow. |
| `--skip-ssrf-check` | `--url` | Skip the SSRF checks for this add. |
| `--disable-http2` | `--url` | Do not negotiate HTTP/2; leave HTTP/1.1. |
| `--callback-port <port>` | `--url` | Local port to bind the OAuth callback listener to during add-time authorisation. Defaults to an OS-assigned ephemeral port. |

The `--url` forms:

| Form | Example |
| --- | --- |
| Remote MCP endpoint URL | `https://mcp.notion.com/mcp` |
| MCP community-registry URL | `https://registry.modelcontextprotocol.io/v0/servers/fetch-mcp/versions/latest` |
| Server-manifest URL (a `server.json` or `server.yaml` body) | `https://example.com/mcp/opine/server.yaml` |
| Docker Hardened Image ref | `dhi.io/fetch-mcp:latest` or `dhi.io/fetch-mcp@sha256:…` |

Other image refs — inputs without `://` that are not `dhi.io`, such as
`docker.io/foo:tag` — are **not** accepted. Use a server manifest instead.

### The SSRF guard

A `--url` whose host resolves to a private, RFC1918, loopback, link-local or
cloud-metadata address is fetched anyway, but **flagged**: the add proceeds and
a warning naming the resolved address is printed. The posture is
warn-and-proceed, not block, because some legitimate servers live on private
networks (split-horizon DNS, internal load balancers, VPN-only endpoints).

`--skip-ssrf-check` disables both the endpoint check and the OAuth-metadata
redirect check, and silences the warning. Use it only for URLs you control.

### The `--header` rule

`--header` adds an HTTP header to every request sent to a remote `--url`
endpoint, in curl convention `Header-Name: header value`. Repeat the flag for
more headers; each header name may be given once. Headers the transport owns
(`Host`, `Content-Length`, `Connection`, `Proxy-*`, …) are rejected.

A header value may reference a secret with `${placeholder}`. The placeholder
is stored in the registration as typed; the secret itself never is. It is read
from the encrypted secret store and substituted when a sandbox connects.

```bash
sbx mcp add acme --url https://mcp.acme.com/mcp \
  --header 'Authorization: Bearer ${api-key}'
sbx secret set mcp:acme:api-key
```

Two constraints:

- `--header` is refused with `--command` and `--local`. Only a remote
  endpoint can carry headers.
- Header secrets are read from the **local** secret store, so a
  header-bearing server only works where this host connects it. Adding one
  while your MCP gateway is the hosted (SaaS) one is rejected rather than
  registered with headers that would be silently dropped — unless the server
  carries a hand-supplied OAuth override, which the host connects directly in
  every gateway mode.

### OAuth for remote servers

Two options, and they are independent:

- `--oauth-authorization-server` supplies the authorization-server metadata
  for a server that publishes no well-known RFC 9728/8414 metadata. It is a
  local file path or an http(s) URL to a JSON document with
  `authorization_endpoint` and `token_endpoint`. `--client-id` is required
  alongside it **unless** the metadata advertises a `registration_endpoint`,
  in which case a client is registered dynamically (RFC 7591) and
  `--client-id` may be omitted.
- `--client-id` supplies a **pre-registered** OAuth client. It may be given
  without `--oauth-authorization-server`: the server's authorization metadata
  is then discovered normally and the supplied client is attached to it. This
  is the right mode for a server whose discoverable metadata exposes no
  `registration_endpoint` but which accepts a client id you registered ahead
  of time.

There is **no `--client-secret` flag**. A confidential client's secret lives
in the secret store, in the global scope, under the service name
`mcp:<server>:client_secret`:

```bash
sbx secret set mcp:slack:client_secret
```

Run it with no `-t`, so the value is read from stdin rather than landing in
your shell history. The secret is never written to the MCP registration on
disk. Remove it later with `sbx secret rm mcp:slack:client_secret`.

The stored secret is bound to the OAuth identity (client id, issuer and token
endpoint) that first used it. Re-registering the same server name against a
different client does **not** reuse it. To let a new identity claim the
existing secret, drop the recorded binding with
`sbx secret rm mcp:slack:client_secret:identity`.

Two rules on the discovered path:

- If the discovered authorization metadata has **no** `registration_endpoint`,
  Dynamic Client Registration is impossible, so `--client-id` is **required**;
  the add fails without it.
- A stored client secret is **required** when the server's advertised
  `token_endpoint_auth_methods_supported` (RFC 8414) does not include
  `none` — that is, it accepts only confidential clients. Registration still
  succeeds without one, but the add-time authorisation is skipped; store the
  secret and run `sbx mcp auth <server>` to finish. When the list includes
  `none`, a public/PKCE client is allowed and `--client-id` alone is enough.
  If the server advertises no `token_endpoint_auth_methods_supported` at all
  — the field is optional in RFC 8414 — the requirement cannot be determined
  and the add proceeds.

### The scope precedence rules

This is the rule people get wrong. From highest to lowest, the scope set
requested at authorisation time is:

1. `--no-scope` — suppress **all** scope fallbacks; the server applies its own
   default grant.
2. An explicit `sbx mcp auth --scope`.
3. The set recorded at `sbx mcp add` time.
4. The scope set the **resource** says it requires, from its RFC 9728
   protected-resource metadata or its `WWW-Authenticate` challenge.
5. Whichever of `openid`, `email`, `profile` and `offline_access` are
   advertised. Other advertised scopes are excluded from this fallback.

If no scopes are selected, the `scope` parameter is omitted so the
authorization server can apply its own default grant (RFC 6749 §3.3).

A resource that publishes a required set therefore gets it requested with no
flag at all, and the consent block marks that set as **derived** rather than
**chosen**.

`--scope` and `--no-scope` cannot be combined, on either `sbx mcp add` or
`sbx mcp auth`.

Scope values may be URN-shaped (`urn:ietf:params:oauth:scope:mail`) or
URL-shaped (`https://www.fastmail.com/dev/mcp`). Neither needs quoting: a
scope token cannot contain a space or a quote.

**Recognition is not a promise.** Scopes you name are checked against the
union of two documents a server can publish: the authorization server's
RFC 8414 `scopes_supported` and the resource's own RFC 9728 metadata. A scope
in neither prints a warning naming the offender but is still requested — a
mismatch is often the server's own documentation gap. A scope in
`scopes_supported` is a scope the server *supports*, not one it will grant
this client.

### `offline_access` and refresh tokens

`offline_access` is one of the four scopes in the last fallback tier
(`openid`, `email`, `profile`, `offline_access`). It is requested when
nothing else selected a set and the server advertises it.

The refresh path is separate from the scope list. From the `sbx mcp auth`
help: if the stored credential is expired, `sbx` asks the control plane to
refresh it first, and **only falls back to interactive OAuth when the refresh
needs user consent**. So a routine expiry is not an interruption; only a
refresh that needs your consent reopens the browser.

> The specific link between the `offline_access` scope and the issuing of a
> refresh token is standard OAuth behaviour, but the ledger does not state it
> and neither does the local help text. **Not verified in the ledger.** What
> is verified is the fallback set and the refresh-first behaviour above.

### `--resource`

Every authorisation request, code exchange and token refresh names the server
the token is for, in the `resource` parameter the MCP authorisation
specification requires (RFC 8707). That value is normally **derived**: from
the server's own RFC 9728 protected-resource metadata when it publishes some,
and otherwise from the `--url` endpoint.

`--resource` replaces that derivation. Use it when the derived value is
wrong — most often a server whose published identifier is its bare origin
(`https://api.example.com`) while its endpoint has a path
(`https://api.example.com/mcp`), which is indistinguishable from publishing
nothing. An authorization server entitled to reject an unknown target answers
`invalid_target`.

The value must be an absolute URI with a scheme and a host, and RFC 8707 §2
forbids a fragment; a bad value fails the add rather than being repaired
later. It is sent **verbatim**. It outranks both the published value and the
URL derivation, and it is recorded on the registration, so there is no
`sbx mcp auth --resource`: one value is used by the add-time authorisation,
every later `sbx mcp auth`, and every token refresh. To change it, remove the
server and add it again.

### `--callback-port`

Binds the OAuth callback listener to a specific local port during add-time
authorisation, instead of an OS-assigned ephemeral port. Use it when the port
must be pre-registered in the OAuth application's allowed redirect URIs. It
applies to `--url` remote OAuth servers and is ignored, with a warning, if the
server turns out not to need OAuth.

### `--skip-auth`

Registers an OAuth server without starting the hosted OAuth flow. The
registration is stored; the authorisation is deferred to `sbx mcp auth`.

### The renamed flag

`--skip_auth` was the v0.45 spelling. From v0.48.0 it is `--skip-auth`; the
old spelling is still accepted, but it is hidden and **warns on stderr**
(ledger §3). Change the spelling. Do not filter the warning.

## 2. `sbx mcp catalog`

Lists the server names in Docker's hosted MCP catalog. Reading the catalog
needs **no sandbox, no daemon and no sign-in**.

```bash
sbx mcp catalog
sbx mcp catalog --json
```

These are the names `sbx --cloud create --static-mcp` and
`sbx --cloud mcp load` resolve against; the cloud gateway looks each one up
itself. A local `--static-mcp` set takes names you registered with
`sbx mcp add`, not catalog names.

## 3. `sbx mcp auth`

```
sbx mcp auth [server-name] [flags]
sbx mcp auth status [server-name] [flags]
sbx mcp auth rm [server-name] [flags]
```

| Flag | Meaning |
| --- | --- |
| `--all` | Apply to all registered OAuth servers. |
| `--scope <scope>` | Authorize a specific set of scopes for this run. Repeatable. Overrides the recorded default. |
| `--no-scope` | Request no scopes at all for this run. Cannot be combined with `--scope`. |
| `--format text\|json` | Output format. Default `text`. |
| `--json` | Alias for `--format json`. |
| `--verbose` | Print authorization polling progress. |

`sbx mcp auth <server>` authorises or reauthorises one server. A name with no
local registration is resolved from the Docker MCP catalog and authorised
under its catalog name, so no `sbx mcp add` is required for catalog servers.

`sbx mcp auth status` inspects hosted credential status **without** starting
OAuth or refreshing expired credentials. `sbx mcp auth rm` removes hosted
credentials without removing local MCP server registrations.

For an existing or freshly completed authorisation, the **granted** set — what
the authorization server actually handed over — is reported alongside the
status. An authorization server may grant less than was asked for; when it
restates no set at all, RFC 6749 §5.1 makes that the set that was requested.

In local data-plane mode a refusal prints the requested set, the advertised
set, the scopes the server named, and a narrower retry command. The hosted
control plane reports only that authorization failed or timed out.

## 4. `sbx mcp load` and `sbx mcp unload`

```
sbx mcp load <name> --sandbox <sandbox>
```

Attaches a previously-registered MCP server to a **running** sandbox's
gateway. The server must already exist in the local MCP store — register
first with `sbx mcp add`. Both remote/hosted and local-stdio servers are
supported.

Connected agents see the new server's tools immediately via the standard MCP
`tools/list_changed` notification. No agent restart is required.

```bash
sbx mcp add notion --url https://mcp.notion.com/mcp
sbx mcp load notion --sandbox my-sbx
```

`sbx mcp unload` is new in v0.48.0 (ledger §1) and is the inverse: it takes a
server out of a running sandbox's gateway. It is not in the v0.45.0-rc2 binary
on this Mac.

Under `--cloud` there is no local registration: the cloud gateway resolves the
server name itself, and the sandbox may be given by name or `sbx_` ID.

## 5. Static and dynamic modes

### Static

`--static-mcp` names form the sandbox's **fixed** MCP set:

```bash
sbx run claude . --static-mcp notion,atlassian
sbx run claude . --static-mcp notion --static-mcp atlassian
```

All forms accumulate into the same set. The set is chosen **once at creation
time** and cannot be changed when re-attaching to an existing sandbox. Local
sandboxes take names registered with `sbx mcp add`; cloud sandboxes take names
from `sbx mcp catalog`.

### Dynamic

A dynamic gateway exposes discovery and configuration tools to the agent, so
the agent can find and attach MCP servers itself during a session.

> The dynamic tool names and their exact behaviour are **not in the ledger**.
> The following is observed in the v0.45.0-rc2 binary on this Mac, from its
> own log and description strings:
>
> - `mcp-find` — "Search available MCP servers. Returns servers that can be
>   added to this session via mcp-add."
> - `mcp-add` — attaches a server to the session.
> - `mcp-config-set` — sets configuration for a server.
> - `mcp-exec` and `code-mode` are also registered on the gateway.
>
> The binary also states that on a **static-mcp** sandbox the dynamic tools
> `(mcp-find, mcp-add, mcp-config-set)` are **suppressed**, and only
> `mcp-exec` + `code-mode` remain. Treat the exact tool set as unverified
> until the ledger records it.

The practical difference:

| | Static | Dynamic |
| --- | --- | --- |
| Set decided by | You, at creation | The agent, during the session |
| Can change later | No | Yes, within the session |
| Discovery tools | Not exposed | `mcp-find` and friends |
| Good for | A repeatable, auditable test | An exploratory session |

For automation, use static. A dynamic set means the tool list can change
between two runs of the same script, which is the same class of problem as the
JSON shape change in `03-structured-output-and-parsers.md`.

## 6. The built-in gateway tools

The gateway also carries a set of `sbx_*` tools that let an agent manage
sandboxes directly.

> These tool names are **not in the ledger**. The list below is observed in the
> v0.45.0-rc2 binary on this Mac.

| Group | Tools |
| --- | --- |
| Sandboxes | `sbx_list_sandboxes`, `sbx_create_sandbox`, `sbx_remove_sandbox`, `sbx_stop_sandbox`, `sbx_inspect_sandbox` |
| Templates | `sbx_list_templates`, `sbx_remove_template` |
| MCP | `sbx_list_mcp_servers`, `sbx_add_mcp_server`, `sbx_remove_mcp_server`, `sbx_authorize_mcp_server` |
| Ports | `sbx_list_published_ports`, `sbx_publish_port`, `sbx_unpublish_port` |
| Policy | `sbx_get_policy`, `sbx_policy_check`, `sbx_policy_allow`, `sbx_set_policy_preset` |
| Secrets | `sbx_list_secrets` |
| Kits | `sbx_list_kits`, `sbx_write_kit`, `sbx_remove_kit` |
| Host | `sbx_get_working_dir`, `sbx_describe_projects`, `sbx_scan_host`, `sbx_check_prerequisites` |

Two observations about their descriptions, which matter for how an agent
behaves:

- The read-only `sbx_list_*` and `sbx_get_policy` / `sbx_policy_check` tools
  run without confirmation. The mutating ones do not.
- The tool descriptions tell the agent to **re-check state after a mutating
  operation** rather than assume the result, and to hand the user a command
  rather than run it when the user is present.

## 7. Host stdio servers versus container servers

This is the distinction that decides your whole design.

Each sandbox has **its own Docker Engine**. There is **no path to the host
daemon** (ledger §10). A sandbox cannot reach `docker.sock`, cannot read your
files, and cannot run your binaries.

A `--command` MCP server therefore runs as a **subprocess on the host**,
outside the sandbox. The gateway brokers the connection; the server itself is
a host process.

The `sbx mcp add --command` help is blunt about what that means:

> Local servers are for ad-hoc development only. They have no identity, no
> verifiable supply chain, and no sandboxing. The process runs with your host
> user's full permissions — it can read your filesystem, access your network,
> and call any API your user can. Do not use `--command` with untrusted
> executables.

| | Host stdio server (`--command`) | Server running inside the sandbox |
| --- | --- | --- |
| Runs as | Your host user, outside the sandbox | The sandbox's `agent` user, inside the VM |
| Host filesystem | Full access | None |
| Host network | Full access | Only what the network policy allows |
| Identity | None | The sandbox's |
| Supply chain | Not verifiable | Whatever the sandbox was built with |
| Reachability | The gateway connects to it | Direct, inside the VM |
| Best for | Development, local tools, your own scripts | Anything that must be scoped to the sandbox |

`sbx mcp add --local` is a third thing: it runs a registry OCI server locally
via `docker run` on the host, and supports stdio packages only.

The security boundary in ledger §10 is the hypervisor, and it holds for
everything inside the VM. A host-side stdio server is the one deliberate
crossing, and it crosses with your full user permissions. That is why the
connector below is written to refuse rather than to act.

## 8. This workshop's connector: `connectors/sbx-mcp/`

### What it is

`connectors/sbx-mcp/sbx_mcp.py` is an MCP stdio server that exposes Docker
Sandboxes to an agent. It speaks newline-delimited JSON-RPC 2.0 on stdio,
which is what MCP's stdio transport uses: one JSON object per line, in both
directions.

It imports `sbx_runner` from the neighbouring `connectors/sbx-runner/`
directory, and reuses its version-compat shims and its receipt builder, so the
two connectors never disagree about the `sbx` surface.

Protocol constants: `PROTOCOL_VERSION = "2025-11-25"`, `SERVER_NAME =
"sbx-connectors"`, `SERVER_VERSION = "1.0.0"`.

### The read/write separation

Six tools. Four are read-only and always present. Two are write operations and
are opt-in.

| Tool | Kind | What it does |
| --- | --- | --- |
| `sbx_status` | read | The installed `sbx` version, the daemon state, the sandbox count, and the capability record. |
| `sbx_list_sandboxes` | read | The sandboxes on this host, with agent, status and workspaces. |
| `sbx_inspect_sandbox` | read | The full inspection record for one sandbox: kits, mounts, network policy, secrets, ports. |
| `sbx_policy_check` | read | Whether the network policy would allow a destination, and whether an explicit rule exists. |
| `sbx_run_test` | **write** | Create a disposable sandbox, run one command inside it, return a receipt. |
| `sbx_collect_file` | **write** | Copy one file out of a sandbox onto the host. |

The read tools are always exposed:

```python
READ_TOOLS = ("sbx_status", "sbx_list_sandboxes", "sbx_inspect_sandbox",
              "sbx_policy_check")
WRITE_TOOLS = ("sbx_run_test", "sbx_collect_file")
```

### The two environment variables

```bash
# Comma-separated. Names the write tools this process may expose.
SBX_MCP_ALLOW=sbx_run_test,sbx_collect_file

# Comma-separated. The command prefixes the write path may run.
SBX_MCP_ALLOWED_COMMANDS="npm test,python3 -m pytest"
```

- `SBX_MCP_ALLOW` gates **which write tools exist**. A write tool that is not
  named is not listed by `tools/list` and not callable.
- `SBX_MCP_ALLOWED_COMMANDS` gates **what `sbx_run_test` may run**. It is
  separate, and it is checked on every call.

`allowed_tools()` is the whole policy:

```python
def allowed_tools():
    """The write tools this process may expose. Reads are always on."""
    granted = set(_env_list("SBX_MCP_ALLOW"))
    return READ_TOOLS + tuple(t for t in WRITE_TOOLS if t in granted)
```

`command_permitted()` is a **prefix** match, and the prefix must cover the
program, not merely appear as a substring:

```python
def command_permitted(argv):
    """True when `argv` matches an allow-listed command prefix.

    The prefix must cover the *program*, not just a substring: `npm test`
    allows `npm test -- --watch`, and does not allow `rm`.
    """
    prefixes = allowed_commands()
    if not prefixes:
        return False
    if not argv:
        return False
    for prefix in prefixes:
        parts = prefix.split()
        if argv[:len(parts)] == parts:
            return True
    return False
```

So `SBX_MCP_ALLOWED_COMMANDS="npm test"` permits `npm test`,
`npm test -- --watch` and `npm test -- --reporter=json`, and refuses `rm`,
`npm` on its own, and `sh -c 'npm test'`.

`sbx_run_test` also validates its own arguments before touching anything:
`command` must be a non-empty list of strings; `mode` must be `clone`,
`direct` or `mountless`; `clone` and `direct` require a `workspace`; and
`timeout` must be a positive integer, defaulting to the runner's 900 s.

### Why it refuses with a reason instead of hiding the tool

The file says it plainly:

> The refusal is the point. "The tool is there but it says no and says why" is
> a different thing from "the tool is not there", and the first one can be
> reviewed.

Three concrete reasons this design is better than hiding:

1. **A hidden tool is an ambiguity.** If `sbx_run_test` is simply absent, the
   agent cannot tell whether the capability was never granted, was revoked
   mid-session, was misnamed, or was removed by a policy. A refusal with a
   reason names the cause.
2. **A refusal is reviewable.** A call log that says
   `{"error": "command_not_allowed", "message": "this server may not run that
   command", "data": {"requested": [...], "allowed_prefixes": [...]}}` is
   evidence. A missing tool is not.
3. **A refusal does not invite a workaround.** The server's `initialize`
   instructions tell the model: "A refusal states the reason; do not retry it
   as another command." A tool that is simply absent tends to produce
   attempts to reach the same capability by another route.

Note the split of responsibilities: `SBX_MCP_ALLOW` decides whether a tool
**exists**; `SBX_MCP_ALLOWED_COMMANDS` decides whether a particular **call**
is permitted. Both refusals are explicit.

### The refusal mechanics

`tools/call` distinguishes three failure kinds:

| Situation | Result |
| --- | --- |
| Unknown tool name | JSON-RPC error `-32602` (`unknown tool: …`) |
| Known tool, not enabled | JSON-RPC error `-32601` (`tool not enabled: …`) with `data: {"required_env": "SBX_MCP_ALLOW"}` |
| Tool ran and failed | A **successful** result with `isError: true` and a content block holding `{"error": …, "message": …, "data": …}` |

The third row is the important one. A tool-level failure is a successful
protocol call with an error **content block**, not a protocol error, because
the model is supposed to read it. The same treatment covers an unexpected
exception: the server catches it and returns `{"error": "tool_failed",
"message": …}` rather than dying. A tool must not kill the server.

### Registering it

On the host:

```bash
sbx mcp add sbx --command python3 --args "/absolute/path/to/connectors/sbx-mcp/sbx_mcp.py"
```

Use an absolute path. The host process's working directory is the host's, and
`--dir` sets it explicitly if you want it pinned:

```bash
sbx mcp add sbx --command python3 \
  --args "/absolute/path/to/connectors/sbx-mcp/sbx_mcp.py" \
  --dir "/absolute/path/to/connectors/sbx-mcp"
```

Then give a sandbox the tools, in static mode:

```bash
sbx run claude . --static-mcp sbx
```

Or attach it to a running sandbox:

```bash
sbx mcp load sbx --sandbox my-sbx
```

### Enabling the write path

The read tools work with no environment at all. To enable the write tools,
set the environment **for the server process**. The environment of the `sbx
mcp add` registration is the host's, so export it before starting the server
or set it in the wrapper that launches it:

```bash
export SBX_MCP_ALLOW=sbx_run_test
export SBX_MCP_ALLOWED_COMMANDS="python3 -m pytest"

sbx mcp add sbx --command python3 \
  --args "/absolute/path/to/connectors/sbx-mcp/sbx_mcp.py"
```

To enable everything the connector offers:

```bash
export SBX_MCP_ALLOW=sbx_run_test,sbx_collect_file
export SBX_MCP_ALLOWED_COMMANDS="python3 -m pytest,npm test"
```

### Checking it works

```bash
# What is registered?
sbx mcp ls
sbx mcp inspect sbx
```

The connector's own test file fakes the transport, so it exercises the tool
surface without a daemon or a sandbox:

```bash
python3 connectors/sbx-mcp/test_sbx_mcp.py
```

## What this does not cover

- **The gateway's own protocol.** The gateway is an MCP server that fronts
  other MCP servers. How it brokers, authorises and multiplexes them is not
  in the ledger, beyond the tool surface and the Cedar policy model in
  ledger §5.
- **MCP policy authoring.** Cedar policies, the actions
  (`register`, `invokeTool`, `invokePrimordial`, `readResource`, `getPrompt`)
  and the resources (`MCP::Server`, `MCP::Tool`, `MCP::Resource`,
  `MCP::Prompt`, `MCP::Primordial`) are described in ledger §5. How to write
  one is not covered here.
- **OAuth for a specific provider.** The rules above are general. Which
  endpoints publish `registration_endpoint`, which require a pre-registered
  client, and which advertise a scope set are per-provider facts the ledger
  does not record.
- **The dynamic tool set.** The `mcp-find` / `mcp-add` / `mcp-config-set`
  tools are described from the v0.45.0-rc2 binary only. **Not verified in the
  ledger.**
- **The built-in `sbx_*` gateway tool list.** Also observed in the binary
  only. **Not verified in the ledger.**
- **`sbx mcp unload` details.** It exists from v0.48.0 (ledger §1), but the
  ledger does not describe its flags or its semantics, and it is not in the
  local binary.
- **MCP resources and prompts.** This connector serves an empty list for
  both. It is a tools-only server.
