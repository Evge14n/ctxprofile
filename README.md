# ctxprofile

[![ci](https://github.com/Evge14n/ctxprofile/actions/workflows/ci.yml/badge.svg)](https://github.com/Evge14n/ctxprofile/actions/workflows/ci.yml)

**An offline CLI that costs a single LLM request per context component — and flags the tool definitions you ship on every request but never call.**

You know your agent's requests are expensive. `ctxprofile` tells you *which part of the context* is spending the money: each individual tool schema, the system prompt, each history turn, each tool result — in tokens **and dollars** — from one captured request, with no proxy and no network.

```
model: claude-opus-4-8   input tokens: 900   exact $ (reconciled to usage)

  component             kind            tokens      %    $ cold  $ cached
  write_file            tool_def           238  26.4%   0.00119   0.00012  [UNUSED]
  web_search            tool_def           224  24.9%   0.00112   0.00011  [UNUSED]
  read_file             tool_def           183  20.3%   0.00092   0.00009
  system                system              85   9.4%   0.00043   0.00004
  user[0]               history             51   5.7%   0.00026   0.00003
  assistant[1]          history             41   4.6%   0.00020   0.00002
  user[3]               current_user        41   4.6%   0.00020   0.00002
  tool_result[2]        tool_result         37   4.1%   0.00018   0.00002

  total $ (cold input): 0.00450
  dead tools (shipped, never called): web_search, write_file — $0.00231 wasted every request
```

Half the input cost of that request is two tool definitions the model never used. You pay for them on every single call.

## Install

```
pip install ctxprofile
```

Python 3.11+. Zero runtime dependencies.

## Use

Capture one request/response pair as JSON (the raw Anthropic Messages API request, optionally with the response so costs reconcile to the real `usage`), then:

```
ctxprofile analyze capture.json
ctxprofile analyze capture.json --json           # machine-readable
ctxprofile analyze request-only.json --model claude-sonnet-5
ctxprofile compare before.json after.json        # $ delta of a prompt/tool change
```

The input is either a bare request, or `{ "request": {...}, "response": {...} }`.

`compare` shows exactly what a change costs. Drop a dead tool and see it:

```
compare (A -> B)   model: claude-opus-4-8
  total $ (cold): 0.00450 -> 0.00350   (-0.00100, -22%)

  component               Δ tokens    Δ $ cold  note
  web_search                  -224    -0.00112  removed
  ...
  dead tools: ['web_search', 'write_file'] -> ['write_file']
```

## Lock the context floor and gate it in CI

Treat your static context — the system prompt and the tool set you ship on every
request — like a lockfile. Commit a baseline, and let CI fail any PR that raises
it or adds a tool without a deliberate re-lock.

```
ctxprofile lock --from captures/agent-turn.json    # writes .ctxprofile.lock
ctxprofile ci --lock .ctxprofile.lock captures/*.json
```

```
FAIL captures/agent-turn.json
  - static floor +410 tokens over lock (allowed +0)
  - new tool(s) not in lock: web_search
```

Accepting a rise is a deliberate one-line commit: re-run `ctxprofile lock` and
commit the new `.ctxprofile.lock`.

You can also gate absolute cost and dead tools with a budget:

`ctxbudget.toml`:

```toml
max_input_tokens = 20000
max_total_usd_cold = 0.05
max_dead_tools = 0
```

Drop either into GitHub Actions; it exits `1` on any breach, so the step blocks the PR:

```yaml
- run: ctxprofile ci --budget ctxbudget.toml --lock .ctxprofile.lock captures/*.json
```

Honest scope: the lock covers the **static floor** — the system prompt, the tool
definitions, and the set of tools you ship — which lives in your repo. It does
not lock per-request cost (RAG chunks, history, tool results); that is not in the
repo and moves on every call. The gate fires on the delta of a stable estimator,
so the estimator's bias cancels.

## Audit MCP-server tool bloat across a corpus

Single-request dead-tool detection is noisy — one request calls a couple of
tools and everything else looks idle. The signal is a **corpus**: point
`mcp-audit` at your tool definitions plus a set of `claude -p` / Agent SDK JSONL
traces, and it groups the wasted tokens by MCP server.

```
ctxprofile mcp-audit --defs request.json --traces runs/*.jsonl
```

```
mcp-audit   model: claude-opus-4-8   window: 3 model calls
  server                     tools  tok/req  calls  $/req cold
  mcp__ruflo                     2      130      0     0.00065
  mcp__files                     1       49      2     0.00024
  (local)                        1       40      1     0.00020

  mcp__ruflo: 130 tok/req, 0 calls in 3 model calls — $0.00065 shipped every request
  [window: 3 model calls — a rarely used tool can look unused in a short window]
```

`--defs` is a raw request (it carries the tool schemas, so the tokens are real);
`--traces` are SDK or `claude -p --output-format stream-json` transcripts (they
carry the call counts). It reports a call **rate over a stated window**, never a
boolean "dead" — a tool used once a week looks unused in a short trace.

## What it does

- **Per-component dollars.** Splits the request into `system`, one row **per named tool**, `history`, `tool_result`, and `current_user`, and prices each with the standard Claude rates. Existing tools count tokens; the cost is what you actually pay.
- **Dead-tool detection.** Any tool defined in `tools[]` that never appears in an assistant `tool_use` block is flagged `[UNUSED]`, with the dollars it wastes on every request. This is the cheapest large saving in most agent setups.
- **Cold vs cached.** Shows each component at the cold input rate and at the 0.1× prompt-cache read rate, so you can see what caching is (or isn't) buying you.
- **Exact when it can be.** With a `usage` block present, the total and the dollars are exact; the per-component split is reconciled to that billed total. Without one, everything is a labelled estimate.

## How the split is computed

Tokens per component come from a stable offline heuristic (~4 chars/token). That estimate is only used for the **proportional** split; when the capture includes a real `usage` block, `ctxprofile` scales the split so the components sum to the exact billed input and prices the exact total. So the headline dollars are real; the per-component division is a grounded estimate, marked as such.

## Prior art

Context attribution is not a new idea. `context-lens` and `ContextSpy` are live proxies with dashboards that break a request into components; `context-profiler` is an offline CLI for token distribution. `ctxprofile` is deliberately narrow and complementary: it is offline (no proxy to run), it reports **dollars** rather than only tokens, it splits **per individual tool** rather than one "tool definitions" blob, and it names **dead tools**. Use a proxy dashboard for a live feed; use `ctxprofile` in a script or CI to cost and lint a single captured request.

## Roadmap

- Cache-churn cost: attribute a rebuilt cache prefix to the component that invalidated it (needs two sequential captures).
- RAG-chunk attribution when retrieved context is tagged.
- Optional exact per-component counts via the `count_tokens` endpoint.

## License

MIT © Olga Martynyuk
