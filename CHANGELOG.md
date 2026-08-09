# Changelog

## 0.12.1

- `docs/mcp-tool-anatomy.md`: a survey of 19 published MCP servers — 186 tool
  definitions, ~52,000 tokens and $0.26 on every request, a quarter of a 200K
  context window before the user types. Two thirds of what those definitions
  ship is JSON structure rather than language, measured in characters so the
  finding does not rest on a tokenizer, with a sensitivity table showing how far
  that assumption has to bend before the conclusion flips. The corpus ships as
  `examples/mcp-survey.json` and `tools/tool_anatomy.py` recomputes every figure.

## 0.12.0

- `churn` command: attribute a rebuilt prompt cache to the component that
  invalidated it. Compares two consecutive captures in the API's render order —
  tools, then system, then messages — and blames the first entry that actually
  differs. Appending a message is not a divergence, so a growing conversation
  reports nothing. A rebuild with an identical prefix is labelled a TTL expiry
  when the gap exceeds the cache lifetime and `unexplained` when it does not,
  rather than blaming a component the data does not implicate. The cost column
  is the excess over what a cache read would have cost, not the whole write.
  This closes the last item the design doc listed as designed-but-not-shipped.

## 0.11.2

- `--online` now reports how far the offline estimator was from the measured
  value on the rows it could check, so the rows it could not measure come with a
  known error size and direction instead of an unqualified guess.

## 0.11.0

- `analyze --online` and `mcp-audit --online`: measure the system prompt and each
  tool definition against the `count_tokens` endpoint instead of estimating them.
  Each tool row is its own leave-one-out marginal — what removing it actually
  saves — and the shared tool-block overhead gets its own row rather than being
  folded into the tools. Measured rows are marked `*` and carry `"exact": true`
  in `--json`. Opt-in via the new `[online]` extra; the core stays offline and
  dependency-free, and `--online-max-calls` refuses an oversized run up front.
- Fix (correctness): a tool the model called in the capture's **response** was
  reported as never called, so the dead-tool figure was overstated by the cost of
  whichever tool the turn actually used — the normal shape of a captured agent
  turn, on the one signal the docs graded exact.
- Fix (correctness): a billed total of zero no longer zeroes every component
  while still claiming reconciliation to usage.
- Fix (correctness): two tools sharing a name no longer each bill the whole
  measured tool block; neither claims the measurement.
- Fix: a usage block that is present but inconsistent with the request is
  reported as such, instead of the header claiming there was no usage block.
- Fix: `capture` delivers the upstream response even when writing the capture
  fails, and tolerates malformed streaming events instead of dropping a response
  the caller was already billed for. Upstream network errors return 502 rather
  than a dropped connection.
- Fix: API errors from `--online` (bad key, rate limit, unreachable host) print
  the usual one-line `error: ...` and exit 2 instead of an SDK traceback.
- Fix: `tools/collect_mcp_tools.py` passes each server's configured `env`, so
  servers needing credentials no longer contribute zero tools; a partial
  collection now exits non-zero instead of looking like a complete measurement.
- The `[online]` extra requires `anthropic>=0.41` — `client.messages.count_tokens`
  does not exist in 0.40.

## 0.10.1

- Fix: support both MCP SDK generations. 2.0 renamed the server class from
  `FastMCP` to `MCPServer`, which broke the optional server on a fresh install;
  the import now resolves either. CI runs the MCP tests against 1.28 and 2.0.
- The MCP server's tests skip cleanly when the optional extra isn't installed.

## 0.10.0

- `tools/collect_mcp_tools.py`: connect to the MCP servers in a client config,
  read their real tool schemas, and write an analyzable request — so you can
  measure what your own servers cost per call.
- `docs/case-study-mcp.md`: a real measurement of four popular MCP servers —
  46 tool definitions, ~8,300 input tokens and about $0.04 on every request
  before the user types anything, with the per-server breakdown.

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
