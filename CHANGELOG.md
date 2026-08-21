# Changelog

## 0.16.0

- `ctxprofile collect` — the MCP collector is a command instead of a script in the
  repo. The README told a pip-installed user to run `tools/collect_mcp_tools.py`,
  which a pip install does not have; measuring your own servers meant cloning
  first. The script stays as an alias.
- **The collector was broken against `mcp` 2.0.** The SDK renamed `Tool.inputSchema`
  to `input_schema`, and the collector read only the old name, so every server
  returned zero tools. Both spellings are read now, and a test covers both so the
  existing compatibility matrix catches the next rename. Found by installing the
  wheel into a clean venv and running the whole loop, which is the only place the
  new SDK gets used — the dev environment still pins 1.27.
- Errors name what went wrong. `OSError` keeps the errno in `args[0]`, so reading
  `args` blindly turned a missing file into `error: 2`.

## 0.15.0

- `mcp-audit` gains an `unreachable` column: the share of each server's bill that
  no tool call can reference. That is the table where you decide which server to
  remove, and notion being 71% unreachable changes what to do about it. The share
  is always the offline estimate, capped at the server's own weight, so a measured
  token count with an estimated remainder cannot produce more than 100%.
- `analyze` prints a one-line pointer when a request carries removable weight, so
  someone who never runs `lint` still learns it is there.
- A figure in the survey and the README, generated from the committed corpus by
  `tools/render_survey_chart.py`. The generator refuses to write an image whose
  labels run past the edge — a clipped label is a silent failure otherwise.

## 0.14.0

- `lint` command: finds tool-definition weight that no tool call can reference —
  `$defs` entries no `$ref` reaches (transitively, from the schema root) and
  JSON Schema document annotations. On the committed 19-server corpus that is
  **16,058 of 52,035 tool-definition tokens, 30.9%, $0.08 per request**, almost
  all of it one pattern: Notion copies a shared `$defs` registry into every tool,
  and eighteen of its twenty-four tools reference none of it.
- Both checks are restricted to things that are provably free to remove.
  `additionalProperties` (7.2% of the corpus), `default` and `title` were
  measured and deliberately left alone: the model can read them, so dropping
  them is a judgement call rather than a free win.
- `--write-slim` writes the request with that weight stripped, so the claim can
  be checked rather than taken. A test asserts that `analyze` on the slimmed file
  falls by exactly the amount `lint` promised.
- `--online` measures the saving against `count_tokens` (three calls: the
  empty-tools floor, the full tool block, the slimmed one).
- `lint_tool_schemas` added to the optional MCP server, so an agent can lint the
  MCP schemas it is itself carrying.
- Reference resolution handles the cases that would produce a *false* positive —
  the dangerous direction. JSON Pointer escaping (`~1`, `~0`), pointers into a
  definition rather than at it, references across `$defs` and `definitions`. A
  schema holding an anchor, a URI reference or a `$dynamicRef` keeps all of its
  definitions, and keeps `$id`/`$anchor`, rather than being guessed at.
- `stderr` is forced to UTF-8 alongside `stdout`: legacy Windows code pages
  cannot encode the `—` and `…` the tables use.

## 0.13.1

- `tools/bpe_probe.py` and a rewritten section of the anatomy survey: the split
  between language and JSON structure is now measured against four public BPE
  tokenizers instead of resting on a sensitivity argument. The result reverses
  the assumption behind that argument — JSON structure is *less* compressible
  than prose (0.72×–0.74× the characters per token), so the flat estimate was
  understating structure rather than overstating it, and its share is 75–76%
  rather than 69%. None of 168 tools comes near the 2.28× that would have been
  needed to reverse the finding. The probe cannot speak for Claude's tokenizer,
  which is not public, and the document says so.

## 0.13.0

- `demo` command: runs on a bundled sample of four real MCP servers, so a fresh
  install has something to show without needing a capture first.
- Long names keep both ends. MCP tools are named `mcp__server__tool`, and cutting
  the tail threw away the only part that identified them — `mcp__playwright__brow`
  was the same row for eight different tools.
- `analyze --top N` shows the priciest components and accounts for the rest in one
  line, instead of printing 46 rows for a request carrying an MCP server.
- The dead-tool line reports a count and three names rather than listing all of
  them; on a four-server request it was over 2,000 characters of names.
- README leads with a demo image generated from a real run by
  `tools/render_terminal_svg.py`, so the picture cannot drift from the output.

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
