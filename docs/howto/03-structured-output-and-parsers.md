# Structured output and parsers

## What this is for

Every `sbx` command that prints a result can print it as JSON or YAML. v0.48.0
changed the **shape** of several of those documents, and the change is the
kind that does not announce itself: a script that reads
`{"sandboxes": [...]}` from a v0.49 CLI gets an empty result, not an error, and
carries on as if the machine had no sandboxes.

This guide covers how to ask for structured output, which flags conflict with
which, which stream carries what, and how to write a normaliser that survives
both the old and the new shape. The worked normaliser and its tests are at the
end.

## Before you start

- The `sbx` CLI on your `PATH`.
- `jq` for the shell examples.
- Python 3 for the worked normaliser.
- A read of [`00-verified-facts.md`](00-verified-facts.md) §3 and §4. This
  guide is the applied version of those two sections.

```bash
sbx version --json
```

## 1. Ask for structure, not for prose

Two ways to ask, one way to do it:

| Spelling | Status |
| --- | --- |
| `--json` | An **alias** for `--format json` (ledger §4). Works on every version that has a JSON output. |
| `--format json` | The canonical spelling from v0.48.0. |
| `--format yaml` | The same representation as JSON, with `snake_case` field names. |

Field names are `snake_case`. YAML is the same representation, not a different
one, so a YAML consumer and a JSON consumer see the same keys.

Use `--json` if you want one script to work on both sides of the v0.48.0
boundary. Use `--format json` if you are writing for a specific new version
and want the intent to be explicit.

## 2. The mutual exclusivity rule

On commands that share output flags, `--format`, `--json` and `-q` are
**mutually exclusive**. Even `--json --format json` is rejected (ledger §3).

```bash
# Rejected from v0.48.0.
sbx diagnose --json --format json
```

Two consequences:

- Pick one. Do not "belt and braces" a JSON flag with a format flag.
- `-q` is not a quiet-mode modifier for these commands any more. On
  `sbx policy log`, `--quiet` was **removed with no replacement** (ledger §3);
  on `sbx create`, `--quiet` became `--no-progress` (ledger §3).

One trap to know about: on v0.45.0-rc2 the old and new spellings are aliases
rather than competitors, and the combination is accepted. Verified on this
Mac:

```console
$ sbx diagnose --json -o json
{
  "version": "1.0",
  "checks": [
    {
      "name": "CLI binary",
...
```

So a script that passes both flags passes on v0.45 and **fails** on v0.48+.
That is the same class of bug as the shape change, and it is invisible until
the upgrade.

Two more naming details from ledger §3:

- `sbx settings ls` is primary; `sbx settings list` stays an alias.
- The deprecated `--global` flag on secret commands is still accepted, but it
  is hidden and **warns on stderr**. If you capture stderr, that warning is
  now part of your output.

## 3. stdout versus stderr

| Stream | What goes on it |
| --- | --- |
| **stdout** | The result. The document. The rows. |
| **stderr** | Errors, warnings, corrective hints, daemon-start progress, image pulls, kit-build progress. |

(Ledger §4.)

A script that captures only stdout will miss the reason a command failed.
This is not a style point; it is the difference between "the list is empty"
and "the list is empty because the daemon is not running and here is the
warning".

Two practical rules:

1. **Capture both, always.** `cmd > out.json 2> out.err`.
2. **Parse stdout only.** stderr is prose, progress and warnings. It is for
   humans and for your log.

The start-progress case is worth seeing. `sbx exec` on a stopped sandbox
prints the start message on stderr while the command's own output streams
through unchanged:

```console
$ sbx exec <sandbox> -- sh -c 'echo to-stdout; echo to-stderr >&2; exit 7'
exit=7
```

stdout got `to-stdout`; stderr got `Sandbox <sandbox> started successfully` and
`to-stderr`. If you had captured only stdout you would have lost the start
message and the error text, and kept a truncated result.

## 4. What changed shape in v0.48.0

The complete table is in ledger §4. Here is the same information with the
"what breaks" column added.

| Command | Old shape | New shape | What breaks |
| --- | --- | --- | --- |
| `sbx ls` | `{"sandboxes": [...]}` | bare array; rows carry `created_at`, `last_used_at` (RFC 3339) and `detached` | `jq '.sandboxes[].name'` returns nothing |
| `sbx template ls` | `{"images": [...]}` | bare array | same |
| `sbx mcp ls` (local) | `{"gateway": {...}, "servers": [...]}` | bare array; each row carries `gateway` | `.gateway` moves onto each row |
| `sbx policy log` | `allowed_hosts` / `blocked_hosts` | `{"updated_at", "allowed": [...], "blocked": [...]}` | both keys renamed **and** the envelope changed |
| `sbx --cloud policy ls` | camelCase | snake_case | every key you read |
| `sbx ports` | `port` (cloud) | cloud: `sandbox_port` + `url`; local: `host_ip`, `host_port`, `sandbox_port`, `protocol` | `port` renamed |
| `sbx ports --publish/--unpublish` | human-readable lines | result rows with `action: published` / `action: unpublished` | you now have a machine-readable result |
| `sbx volume create\|ls\|inspect` (cloud) | `ID`, `Name`, `CreatedAt` | `id`, `name`, `created_at`; `inspect` defaults to a human view, so ask for `--format json` | `ID` → `id`; and `inspect` needs the explicit format |
| `sbx rm`, `sbx stop`, `sbx template rm`, `sbx volume rm` | one line per target | array of `{action, resource, name, id?, error?}`; actions are lowercase `removed`, `stopped`, `removing`, `stopping`, `failed` | you now get per-target errors |

And: `.sandboxes[].name` becomes `.[].name`.

### The two shapes side by side

`sbx ls --json` on v0.45.0-rc2, observed on this Mac:

```json
{
  "sandboxes": [
    {
      "name": "ai-system-ops-20261016",
      "id": "b4b1da1e-46c2-4e47-af97-e751c7087f1f",
      "agent": "shell",
      "status": "stopped",
      "last_used_at": "2026-10-16T17:17:37.872079Z",
      "created_at": "2026-10-16T17:11:57Z"
    }
  ]
}
```

`sbx ls --json` on v0.48.0+ is the same rows, as a bare array:

```json
[
  {
    "name": "ai-system-ops-20261016",
    "id": "b4b1da1e-46c2-4e47-af97-e751c7087f1f",
    "agent": "shell",
    "status": "stopped",
    "last_used_at": "2026-10-16T17:17:37.872079Z",
    "created_at": "2026-10-16T17:11:57Z",
    "detached": false
  }
]
```

`sbx mcp ls --json` on v0.45.0-rc2, observed on this Mac:

```json
{
  "gateway": {
    "name": "LOCAL",
    "local": true,
    "operator": "managed by you",
    "decision": "local",
    "signed_in_as": "hamanakam1"
  },
  "servers": [
    {"name": "wad-ch-02-beans", "transport": "local stdio", "status": "ready", "type": "local"}
  ]
}
```

From v0.48.0 the servers are a bare array and each row carries its own
`gateway`.

`sbx policy log --json` on v0.45.0-rc2, observed on this Mac:

```json
{
  "blocked_hosts": [],
  "allowed_hosts": []
}
```

From v0.48.0 the envelope is `{"updated_at", "allowed": [...], "blocked": [...]}`
and each entry carries `sandbox_id`, `host`, `proxy_type`, `rule`, `reason`,
`count`, `first_seen`, `last_seen`.

## 5. Why parsing human-readable output is a bug

The human table for `sbx ls` looks like this:

```
SANDBOX                         AGENT    STATUS    PORTS   WORKSPACE
ai-system-ops-20261016          shell    stopped           -
m15219-v44-benchmark-20261016   shell    stopped           /tmp/m15219-v44-sandbox-benchmark (missing)
```

Scraping that is a bug for five reasons, in increasing order of how much they
will cost you:

1. **It loses data.** The table has no `id`, no `created_at`, no
   `last_used_at`, no `detached`. The JSON has all of them.
2. **It is lossy about strings.** The workspace column is truncated and
   decorated (`(missing)` is appended to a path). You cannot recover the path
   from the table.
3. **It is not a contract.** Nothing promises the column set, the order, the
   padding, or the wording. A cosmetic change to the table breaks your
   parser, and the breakage is indistinguishable from "the field is empty".
4. **It loses the reason for a failure.** Errors go to stderr, so a scraper
   of stdout never learns why the command produced nothing.
5. **It has no error channel.** The JSON forms carry an `error` field per
   target (`sbx rm`, `sbx stop`, `sbx template rm`, `sbx volume rm`). A line
   of prose does not.

The v0.48.0 change to `sbx rm` and `sbx stop` shows what you get when you use
the structured form: an array of `{action, resource, name, id?, error?}`, with
lowercase actions `removed`, `stopped`, `removing`, `stopping`, `failed`. That
is a per-target result with a per-target error. The human form is one line per
target and cannot say which one failed.

If you must scrape prose — a legacy script, a tool you do not control — treat
it as a compatibility shim with a version check and a loud failure, not as the
primary path. And never scrape the prose **and** the JSON in the same script
without a version gate, or the two will disagree.

## 6. Writing a normaliser

### 6a. The rules

1. **Test the type, not the key.** A document is either a list or a mapping.
   Check `isinstance(document, list)` first. Do not ask "does it have a
   `sandboxes` key", because that is a question about the *version*, and you
   want the question to be about the *data*.
2. **Accept every shape you have ever seen**, not just the two you remember.
3. **Raise on an unusable shape.** Returning an empty list for "I did not
   understand this document" is the worst possible answer, because an empty
   list is a valid answer.
4. **Drop non-dict rows** rather than letting them crash a later
   `row["name"]`.
5. **Keep the normaliser pure.** It takes a parsed document and returns a
   list. It does not run `sbx`.

### 6b. The worked normaliser

Save this as `sbx_shapes.py`:

```python
"""Normalise `sbx` JSON documents across the v0.45 and v0.48+ shapes.

The v0.48.0 release changed the shape of several `sbx` JSON documents: the
list wrappers went away and the envelopes were renamed. A consumer that
assumes one shape breaks silently on the other, and the failure is an empty
list rather than an error.

Nothing here runs `sbx`. These are pure functions over parsed documents, so
they can be tested without a daemon, a login or a hypervisor.
"""

from __future__ import annotations

__all__ = [
    "ShapeError",
    "normalise_sandbox_list",
    "normalise_mcp_servers",
    "normalise_policy_log",
]


class ShapeError(ValueError):
    """The document is not a shape this normaliser understands."""


def _rows(document, *keys):
    """Pull a list of dict rows out of a document, whatever shape it has.

    Accepts:
      * a bare list                     (v0.48+)
      * a mapping with one of `keys`   (v0.45 and earlier)
      * a single mapping that looks like a row
    """
    if isinstance(document, list):
        return [row for row in document if isinstance(row, dict)]
    if isinstance(document, dict):
        for key in keys:
            value = document.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        # A single row is a reasonable answer for a one-item document.
        if "name" in document:
            return [document]
    raise ShapeError(f"no row list found; expected a list or one of {keys}")


def normalise_sandbox_list(document):
    """Return the sandbox rows from `sbx ls --json`.

    v0.45 returned {"sandboxes": [...]}. v0.48.0 returns the array directly.
    """
    return _rows(document, "sandboxes", "items", "data")


def normalise_mcp_servers(document):
    """Return the server rows from `sbx mcp ls --json`.

    v0.45 returned {"gateway": {...}, "servers": [...]}. v0.48.0 returns a bare
    array whose rows each carry their own "gateway" key.
    """
    return _rows(document, "servers", "items", "data")


def normalise_policy_log(document):
    """Return (allowed, blocked) from `sbx policy log --json`.

    v0.45 returned {"allowed_hosts": [...], "blocked_hosts": [...]}.
    v0.48.0 returns {"updated_at": ..., "allowed": [...], "blocked": [...]}.
    """
    if not isinstance(document, dict):
        raise ShapeError("policy log is not an object")
    allowed = document.get("allowed")
    blocked = document.get("blocked")
    if isinstance(allowed, list) and isinstance(blocked, list):
        return allowed, blocked
    # The v0.45 shape.
    allowed = document.get("allowed_hosts")
    blocked = document.get("blocked_hosts")
    if isinstance(allowed, list) and isinstance(blocked, list):
        return allowed, blocked
    raise ShapeError("policy log has neither the new nor the old shape")
```

Note three choices in `_rows`:

- The `items` and `data` aliases are **not** in the ledger. They are
  defensive: if a future release renames the wrapper again, this keeps
  working rather than silently returning nothing. If you would rather be
  strict, drop them — but then a rename becomes an outage.
- The single-mapping case (`{"name": "a"}` → `[{"name": "a"}]`) is a
  convenience for one-item documents. It is not a shape any `sbx` command
  documents.
- `ShapeError` is raised for an unusable document. A caller that catches it
  knows the difference between "no sandboxes" and "I did not understand
  this".

### 6c. The tests

Save this as `test_sbx_shapes.py`:

```python
"""Tests for the sbx shape normalisers.

Run with:  python3 test_sbx_shapes.py
"""

import unittest

import sbx_shapes


class SandboxListTests(unittest.TestCase):
    def test_wrapped_shape_v045(self):
        document = {"sandboxes": [{"name": "a"}, {"name": "b"}]}
        self.assertEqual(
            sbx_shapes.normalise_sandbox_list(document),
            [{"name": "a"}, {"name": "b"}],
        )

    def test_bare_shape_v048(self):
        document = [{"name": "a"}, {"name": "b"}]
        self.assertEqual(
            sbx_shapes.normalise_sandbox_list(document),
            [{"name": "a"}, {"name": "b"}],
        )

    def test_both_shapes_give_the_same_answer(self):
        rows = [{"name": "a"}, {"name": "b"}]
        self.assertEqual(
            sbx_shapes.normalise_sandbox_list({"sandboxes": rows}),
            sbx_shapes.normalise_sandbox_list(rows),
        )

    def test_single_object(self):
        self.assertEqual(
            sbx_shapes.normalise_sandbox_list({"name": "a"}), [{"name": "a"}]
        )

    def test_non_dict_rows_are_dropped(self):
        document = [{"name": "a"}, "junk", 3, None]
        self.assertEqual(
            sbx_shapes.normalise_sandbox_list(document), [{"name": "a"}]
        )

    def test_unusable_shapes_raise(self):
        for bad in (None, 42, "text", {"nothing": 1}, {"sandboxes": "nope"}):
            with self.assertRaises(sbx_shapes.ShapeError):
                sbx_shapes.normalise_sandbox_list(bad)

    def test_an_unusable_shape_is_not_an_empty_list(self):
        # The whole point: "I did not understand this" must not look like
        # "there are no sandboxes".
        with self.assertRaises(sbx_shapes.ShapeError):
            sbx_shapes.normalise_sandbox_list({"unexpected": []})


class McpServerListTests(unittest.TestCase):
    def test_wrapped_shape_v045(self):
        document = {
            "gateway": {"name": "LOCAL", "local": True},
            "servers": [{"name": "beans"}],
        }
        self.assertEqual(
            sbx_shapes.normalise_mcp_servers(document), [{"name": "beans"}]
        )

    def test_bare_shape_v048(self):
        document = [{"name": "beans", "gateway": {"name": "LOCAL"}}]
        self.assertEqual(
            sbx_shapes.normalise_mcp_servers(document),
            [{"name": "beans", "gateway": {"name": "LOCAL"}}],
        )


class PolicyLogTests(unittest.TestCase):
    def test_old_shape(self):
        document = {"allowed_hosts": [{"host": "a"}], "blocked_hosts": []}
        allowed, blocked = sbx_shapes.normalise_policy_log(document)
        self.assertEqual(allowed, [{"host": "a"}])
        self.assertEqual(blocked, [])

    def test_new_shape(self):
        document = {
            "updated_at": "2026-10-22T00:00:00Z",
            "allowed": [{"host": "a"}],
            "blocked": [{"host": "b"}],
        }
        allowed, blocked = sbx_shapes.normalise_policy_log(document)
        self.assertEqual(allowed, [{"host": "a"}])
        self.assertEqual(blocked, [{"host": "b"}])

    def test_unusable_shape_raises(self):
        for bad in (None, [], {"allowed": "nope"}):
            with self.assertRaises(sbx_shapes.ShapeError):
                sbx_shapes.normalise_policy_log(bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

Run it:

```bash
python3 test_sbx_shapes.py
```

### 6d. Using it

```python
import json
import subprocess
import sys

import sbx_shapes

proc = subprocess.run(
    ["sbx", "ls", "--json"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    check=False,
)
if proc.returncode != 0:
    # The reason is on stderr. Do not discard it.
    print(proc.stderr.decode("utf-8", "replace"), file=sys.stderr)
    sys.exit(proc.returncode)

try:
    rows = sbx_shapes.normalise_sandbox_list(json.loads(proc.stdout))
except sbx_shapes.ShapeError as exc:
    print(f"unrecognised sbx ls shape: {exc}", file=sys.stderr)
    sys.exit(2)

for row in rows:
    print(row.get("name"), row.get("status"))
```

### 6e. The same thing in `jq`

```bash
sbx ls --json 2>/dev/null | jq -r '
  (if type == "array" then .
   elif (.sandboxes? | type) == "array" then .sandboxes[]
   else error("unrecognised sbx ls shape")
   end)
  | .name
'
```

The `error(...)` matters. Without it, an unrecognised shape produces an empty
result and the pipeline reports success.

## 7. The same rule for the other v0.48.0 changes

| Command | Normalise |
| --- | --- |
| `sbx template ls` | Same as `sbx ls`: `{"images": [...]}` → bare array. |
| `sbx --cloud policy ls` | camelCase → snake_case. Map the keys explicitly rather than lowercasing: `CreatedAt` and `created_at` are different keys, and a blanket lowercase is a guess. |
| `sbx ports` | cloud `port` → `sandbox_port` + `url`; local gets `host_ip`, `host_port`, `sandbox_port`, `protocol`. |
| `sbx rm` / `sbx stop` | One line per target → an array of `{action, resource, name, id?, error?}`. Check `action` against the lowercase set, and handle `failed` with the `error` field. |
| `sbx volume create\|ls\|inspect` (cloud) | `ID`/`Name`/`CreatedAt` → `id`/`name`/`created_at`. `inspect` defaults to a human view, so ask for `--format json` explicitly. |

## What this does not cover

- **Every command's JSON shape.** The table in §4 is the set the ledger
  verified. Commands not in that table are not documented here.
- **YAML specifics.** The ledger says YAML is the same representation with
  `snake_case` field names. It does not record how YAML is emitted for
  multi-document output, or how empty lists are represented.
- **The `--format` flag on commands that do not share output flags.** Some
  commands have their own format vocabulary. `sbx diagnose` gained
  `table|json|yaml|github-issue` in v0.48.0 (ledger §3).
- **Schema stability.** Nothing here promises the JSON is a stable API. The
  ledger records that it changed once, in v0.48.0, and calls it a breaking
  change. A normaliser buys you compatibility, not a contract.
- **The `mcp-config-set` and other gateway tool payloads.** Those are MCP
  tool results, not `sbx` CLI output, and they are not covered by the ledger.
  See `04-mcp-connectors.md`.
