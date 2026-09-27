"""Execution accuracy: does a predicted result set contain the gold result set?

Rules:
- Same number of rows.
- Every gold column must match a distinct predicted column; extra predicted columns are allowed.
- Rows are compared as a multiset (order ignored). "Top N" questions are still checked, because the gold LIMIT fixes
  which N rows belong in the set.
- Numbers are compared at 4 significant figures, so 1110.8 and 1110.80000001 match; strings must match exactly.
"""
from collections import Counter
from decimal import Decimal
from itertools import product


def norm(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float, Decimal)):
        f = float(v)
        return 0.0 if f == 0 else float(f"{f:.4g}")
    return str(v).strip()


def results_match(gold_rows: list[list], pred_rows: list[list]) -> bool:
    if len(gold_rows) != len(pred_rows):
        return False
    if not gold_rows:
        return True
    g_cols = list(zip(*[[norm(v) for v in r] for r in gold_rows]))
    p_cols = list(zip(*[[norm(v) for v in r] for r in pred_rows]))
    candidates = [[j for j, pc in enumerate(p_cols) if Counter(pc) == Counter(gc)] for gc in g_cols]
    if any(not c for c in candidates):
        return False
    target = Counter(zip(*g_cols))
    for choice in product(*candidates):
        if len(set(choice)) != len(choice):
            continue
        if Counter(zip(*[p_cols[j] for j in choice])) == target:
            return True
    return False
