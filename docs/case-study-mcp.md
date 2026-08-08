# What four popular MCP servers cost you on every request

A measurement, not a hypothetical. Install four widely-used MCP servers, open a
session, and before you type a single character your request already carries
**46 tool definitions**.

## Setup

Four servers, all public, all common:

| Server | Package |
| --- | --- |
| filesystem | `@modelcontextprotocol/server-filesystem` |
| memory | `@modelcontextprotocol/server-memory` |
| sequential-thinking | `@modelcontextprotocol/server-sequential-thinking` |
| playwright | `@playwright/mcp` |

`tools/collect_mcp_tools.py` connects to each over stdio, lists its real tools,
and writes them into an Anthropic-shaped request. Then:

```
ctxprofile analyze examples/mcp-real.json
```

## The result

```
model: claude-opus-4-8   input tokens: 8299   estimated (no usage block)
```

**~8,300 input tokens of tool schemas on every request.** At Opus input pricing
that is **$0.041 per request** — about **$41,000 per million requests**, paid
whether or not the model calls any of them.

Tool definitions were **99.8%** of that request's input. The user message was
rounding error.

## Grouped by server

```
ctxprofile mcp-audit --defs examples/mcp-real.json --traces run.jsonl
```

```
  server                     tools  tok/req  calls  $/req cold
  mcp__playwright               24     4269      0     0.02135
  mcp__memory                    9     1134      0     0.00567
  mcp__sequentialthinking        1     1021      0     0.00511
  mcp__filesystem               12     1858      2     0.00929
```

Two things stand out.

**Playwright is half the bill.** 24 browser tools, 4,269 tokens per request. If
the session never touches a browser — and most coding sessions don't — that is
pure overhead, on every call, forever.

**One tool costs 1,021 tokens.** `sequentialthinking` ships a single tool whose
schema is larger than the entire filesystem server's twelve. A single tool can
quietly be your most expensive one; you would never guess that from the server
count.

In the trace above only the filesystem server was ever called. The other three —
6,424 tokens, $0.032 per request — were shipped and ignored.

## What this is and isn't

- The per-component token numbers are **estimates** (a stable ~4 chars/token
  heuristic). Against a real captured response, `ctxprofile` reconciles the split
  to the exact billed total; here there is no `usage` block, so the figure is
  labelled estimated. Treat the magnitude, not the last digit.
- Dollars use list input pricing. With prompt caching a stable tool prefix is
  much cheaper to re-read — that is exactly why `ctxprofile` reports a cold
  upper bound and a measured blended figure separately.
- "0 calls in 4 model calls" is a **rate over a stated window**, not a verdict. A
  tool used once a week looks unused in a short trace. Audit over a real corpus
  before disabling anything.

## Reproduce it

```
git clone https://github.com/Evge14n/ctxprofile && cd ctxprofile
pip install -e ".[mcp]"
python tools/collect_mcp_tools.py your-mcp-config.json -o mine.json
ctxprofile analyze mine.json
```

Point it at your own MCP config. The interesting question isn't what these four
servers cost — it's what *yours* do.
