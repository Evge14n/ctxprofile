from __future__ import annotations


def apportion(weights: list[int], total: int) -> list[int]:
    """Split `total` across `weights` so the parts are integers that sum to `total`.

    Largest-remainder (Hamilton) method: floor every share, then hand the leftover
    units to the largest fractional remainders. Every part is non-negative and the
    sum is exact, which is what makes it safe to reconcile an estimated split
    against a billed total without silently losing or inventing tokens.
    """
    if not weights:
        return []
    if total <= 0:
        return [0] * len(weights)

    weight_sum = sum(weights)
    if weight_sum <= 0:
        # No signal to split on: spread evenly rather than dropping the total.
        base, extra = divmod(total, len(weights))
        return [base + (1 if i < extra else 0) for i in range(len(weights))]

    shares = [w * total / weight_sum for w in weights]
    parts = [int(s) for s in shares]
    shortfall = total - sum(parts)
    order = sorted(range(len(weights)), key=lambda i: shares[i] - parts[i], reverse=True)
    for i in order[:shortfall]:
        parts[i] += 1
    return parts
