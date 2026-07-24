# Changelog

## 0.9.0

- Optional MCP server (`pip install "ctxprofile[mcp]"`, run `ctxprofile-mcp`):
  exposes `analyze_request` and `audit_mcp_servers` so a Claude agent can profile
  its own context. The core CLI stays dependency-free; only this extra needs `mcp`.
- Refactor: one report/audit serializer shared by the CLI and the MCP server.

## 0.8.0

- `capture` reassembles streaming (SSE) responses into a normal message with
  merged `usage` (input side from `message_start`, final `output_tokens` from
  `message_delta`) and rejoined text / tool_use content, so streamed requests —
  the common Claude Code / SDK case — become usable by `analyze`. Non-stream and
  non-JSON bodies are unchanged.

## 0.7.1

- Fix (correctness): reconciliation now apportions the billed total with the
  largest-remainder method, so the per-component split always sums to the exact
  total. Previously a large negative rounding remainder could silently drop
  tokens on a report flagged exact.
- Fix: a malformed line in a trace file no longer aborts `mcp-audit`; unparseable
  lines are skipped.
- Security: sanitize the upstream response id before it reaches a capture
  filename (path traversal), confirm the path stays inside the output directory,
  write captures with `0o600`, and reject a non-integer `Content-Length`.
- Docs: the confidence tables and demo output now say dead-tool token cost is an
  estimate, not exact, and the single-request view says "not called in this
  capture" rather than a permanent "dead" verdict.
- Remove unused pricing dead code.

## 0.7.0

- `ci --format github`: emit a markdown summary for posting as a PR comment.
- Clean CLI errors: unknown model, missing file, or malformed JSON now print a
  one-line `error: ...` and exit 2 instead of a traceback.
- `docs/design.md`: the reasoning and the exact/estimate/not-knowable boundary.

## 0.6.0

- `compare --monthly-requests`: project a per-request $ delta to a monthly volume
  (the number that stops a scroll), and flag when the delta includes per-request
  dynamic movement rather than just your static change.
- Docs: a "What it can and can't tell you" confidence table, an example CI
  workflow (`examples/ctxprofile-ci.yml`), and CONTRIBUTING.

## 0.5.0

- `capture` command: a stdlib forward-proxy. Point a Claude client at it with
  `ANTHROPIC_BASE_URL` and it tees every non-streaming `/v1/messages` request and
  response to a capture file — the input every other command reads. Only bodies
  are stored, never headers, so the API key never lands on disk.

## 0.4.0

- `mcp-audit` command: join tool schema tokens (from a raw request) with call
  counts (from a Claude Agent SDK / `claude -p` JSONL trace corpus) to show
  dead-tool cost grouped by MCP server — the shipped-but-never-called servers
  that quietly cost tokens on every request.
- SDK / `claude -p` stream-json ingestor (`ingest_sdk`): normalizes a run into a
  `Trace` (declared tools, called tools, api calls, total cost).
- Cache-aware effective pricing: when a `usage` block carries a cache split,
  report the blended measured cost alongside the cold uncached upper bound.

## 0.3.0

- `lock` command: write a `.ctxprofile.lock` recording the static context floor
  (system prompt, per-tool-definition tokens, the shipped tool set, and MCP
  servers), like a lockfile for context.
- `ci --lock`: fail when the static floor regresses past a threshold or a new
  tool appears without a deliberate re-lock. Combines with `--budget`.
- `examples/` with a runnable capture, budget, and lock.

## 0.2.0

- `compare` command: token and dollar deltas between two captured requests,
  so you can see exactly what a prompt or tool change costs.
- `ci` command: fail (exit 1) when a capture breaches a TOML budget
  (`max_input_tokens`, `max_total_usd_cold`, `max_dead_tools`,
  `max_wasted_usd_cold`) — drop it into CI to block context bloat and dead tools.

## 0.1.0

First release.

- `analyze` command: cost-attribute one captured Anthropic Messages request per
  context component (system, per tool definition, history, tool_result,
  current_user) in tokens and dollars.
- Dead-tool detection: flags tool definitions shipped on the request but never
  called, with the dollars wasted per request.
- Cold vs prompt-cache-read pricing per component.
- Reconciliation to the exact billed total when a `usage` block is present;
  labelled estimate otherwise.
- Standard-tier prices for `claude-opus-4-8`, `claude-sonnet-5`,
  `claude-haiku-4-5` with derived cache multipliers.
