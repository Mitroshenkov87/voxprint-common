# CLI protocol

`--json` writes UTF-8 lines to stdout and flushes each one. Human text belongs on stderr.

A progress line looks like this:

```json
{"type":"progress","stage":"download","fraction":0.25,"message":"mirror 1"}
```

`fraction` is a number from 0 to 1, or `null` when the stage has no total yet.

The last line is the result. Exactly one result is written, and nothing follows it.

```json
{"type":"result","ok":true,"code":0,"message":"done"}
```

Extra fields are allowed. `type`, `ok`, `code`, and `message` are reserved.

## Exit codes

| Code | Constant | When |
|---|---|---|
| 0 | `EXIT_OK` | Success. `ok` is true |
| 1 | `EXIT_USAGE` | The arguments are missing or unknown |
| 2 | `EXIT_FAILED` | The operation failed |
| 3 | `EXIT_CANCELLED` | Cancelled, or a wait such as the GPU lock expired |

`Session` emits progress lines and then one result. A second result, or progress after the result, raises `ProtocolError`.

Enlarge's own job messages, and its older exit codes, stay in Enlarge. This protocol is the shared envelope.

`voxprint-mdl` uses the same codes and the same result line. `--json` and `--dry-run` may appear before or after the subcommand. `adopt` adds `duplicate` and `reclaimable_bytes`. `cleanup` adds `would_link`, `linked`, `would_delete`, `deleted`, `unique`, `reclaimable_bytes`, and `cleanup_mode`. The installer contract is on the [model downloader](mdl.md) page.
