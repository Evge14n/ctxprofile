from __future__ import annotations

import math

# Offline heuristic token estimator. It only needs to be *stable* and roughly
# proportional across component types; exact totals come from reconciliation
# against a real usage block when one is available (see cost.reconcile).
# Rule of thumb for English/code with Claude tokenizers: ~4 characters per token.
CHARS_PER_TOKEN = 4.0


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN))
