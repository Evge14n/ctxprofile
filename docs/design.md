# Design notes

Short version of why ctxprofile is shaped the way it is, and — more usefully —
what it deliberately refuses to claim.

## The problem

An agent's request is a pile of context: a system prompt, a tool schema for
every tool you *might* call, the whole conversation so far, retrieved chunks,
and tool results. You pay for all of it on every call. The bill tells you the
total; it does not tell you that 40% of it is three tool definitions the model
never calls, or that your "cheap" prompt tweak added a paragraph to a system
block that ships a million times a month.

ctxprofile answers one question — *which part of the context is spending the
money?* — and turns the answer into a check you can put in CI.

## Three decisions

**Offline, not a hosted dashboard.** The incumbents (context-lens, ContextSpy,
the observability SaaS) are live proxies with a UI. That is a different job.
ctxprofile consumes a captured request and runs in a script or a CI step, with
zero network and zero runtime dependencies. The only online piece is `capture`,
a stdlib forward-proxy that tees requests to files; everything downstream is
pure functions over JSON.

**Exact where the data is exact; estimated, and labelled, everywhere else.** The
billed total and the dollars come straight from the `usage` block, including the
cold / cache-write / cache-read split. The *per-component* division is an
estimate — a stable ~4-chars/token heuristic — reconciled so the parts sum to
the exact billed total. So the headline number is real and the split is a
grounded guess, and the output says which is which.

**And measured instead of estimated, when you ask for it.** `analyze --online`
replaces the heuristic for the two components the API serializes independently
of everything else: the system prompt and each individual tool definition. Each
is measured as a *marginal* — the difference between a `count_tokens` call that
carries the component and one that does not — so the number is a real tokenizer
count, not a character ratio. That is the number the dead-tool figure is built
on, which is why it is the one worth paying calls for.

Per-tool marginals use leave-one-out against the full tool set, so the preamble
the API adds once when any tool is present cancels out of every difference. What
is left over after summing them — that preamble, plus any tokenizer boundary
effect at the joins between schemas — is distributed across the tool rows and
reported as `tool_overhead`, so the rows still sum to the measured tool block and
nothing is quietly dropped. Message components stay estimated; there is no way to
difference them out that doesn't cost a call per turn for a number that changes
every request anyway.

Measurement is opt-in and off by default: it needs an API key, the `anthropic`
SDK (`ctxprofile[online]`), and roughly one call per tool. `--online-max-calls`
caps that before anything is spent. The core stays offline and dependency-free.

**A lockfile, because a one-shot analyzer decays to zero.** You read a cost
breakdown once, nod, and never open it again. The habit is a `.ctxprofile.lock`
with the muscle memory of `package-lock.json`: CI recomputes the static context
floor on every PR and fails when it rises or a new tool appears; accepting the
rise is a deliberate re-lock commit. Green-or-red does the caring for you.

The lockfile leans on one honest trick. It stores the *estimator's* raw token
count, which is biased. But the gate fires on the **delta of the same estimator
between two commits**, and the bias is the same on both sides, so it cancels.
The lock records `"estimator": "chars4-v1"`; changing the estimator forces a
re-lock, which is correct.

## What it will not tell you

This is the part that matters. A tool that overclaims here is worse than none.

| Signal | Confidence |
| --- | --- |
| Total input $ (with `usage`) | Exact |
| Blended cache $ (with a cache split) | Exact |
| Tools defined vs. called | Exact |
| Dead-tool token cost | Estimated tokens (~4 chars/token); **measured** with `--online`; list-price rate |
| Per-component token split | Estimate (reconciled to the exact total) |
| System and per-tool tokens with `--online` | Measured — a `count_tokens` marginal, not a heuristic |
| Message / history split with `--online` | Still an estimate; `--online` does not touch it |
| Static-floor regression | Exact delta of a stable estimator |
| Per-request dynamic cost (history, RAG, tool results) | Not locked — moves per call, not in the repo |
| Cache-churn attribution | Not built — needs sequential, timestamped captures |

A caveat about the word *measured*: a marginal is a real tokenizer count, but it
is a difference between two counts, so it inherits any rounding the endpoint
does and any boundary effect between adjacent schemas. Those land in
`tool_overhead` rather than being smeared silently across the rows. The
arithmetic — that measured parts stay untouched, that the rest absorbs exactly
the remainder of the billed total, that nothing sums to more or less than it
should — is covered by tests against a counter with known additivity.

The lock covers only the static floor: the system prompt, the tool schemas, and
the set of tools you ship. Per-request cost lives outside the repo and moves on
every call, so ctxprofile refuses to gate it. `mcp-audit` reports a call **rate
over a stated window**, never a boolean "dead," because a tool used once a week
looks unused in a short trace.

Cache-churn — attributing a rebuilt cache prefix to the component that broke it —
is designed but not shipped: doing it honestly needs at least two sequential
captures of the same lineage *with wall-clock timestamps*, because without them
you cannot tell content invalidation from a plain TTL expiry. `capture` stamps
`captured_at` so the data exists when the feature lands; until then, the tool
says it can't tell you, rather than guessing.
