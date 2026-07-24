# Changelog

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
