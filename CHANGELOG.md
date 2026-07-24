# Changelog

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
