#!/usr/bin/env python3
"""
eval.test_analyzer - Minimal cross-model evaluation matrix & defect detector.

Scans all full_trace.json files, constructs a compact step pass-rate table,
and flags steps that fail universally across all models (defect candidates).

Usage:
    python3 eval/test_analyzer.py
"""

import json
from pathlib import Path
from eval.config import RESULTS_DIR


def main():
    traces = []
    for p in sorted(Path(RESULTS_DIR).glob("*/full_trace.json")):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
                d["_eval_id"] = p.parent.name
                traces.append(d)
        except Exception:
            pass

    if not traces:
        print(f"No evaluation traces found in {RESULTS_DIR}")
        return

    # 1. Numbered Model Legend
    models = []
    for idx, t in enumerate(traces, 1):
        eid = t.get("eval_id") or t["_eval_id"]
        mname = t.get("name") or t.get("model") or "unknown"
        models.append((f"M{idx}", eid, f"{mname} ({eid[:4]})", t))

    print("\nModels:")
    for code, _, lbl, _ in models:
        print(f"  [{code}] {lbl}")

    # 2. Extract step data: (test_name, step_idx, step_name) -> {eid: (passed, score, max_score, err)}
    steps_order = []
    seen_steps = set()
    matrix = {}

    for _, eid, _, t in models:
        tests = t.get("tests", {})
        test_items = tests.items() if isinstance(tests, dict) else enumerate(tests) if isinstance(tests, list) else []
        for tname, tdata in test_items:
            for s in tdata.get("steps", []):
                sidx = s.get("step_index", 0)
                key = (str(tname), sidx, s.get("step_name") or f"Step {sidx+1}")
                if key not in seen_steps:
                    seen_steps.add(key)
                    steps_order.append((key, (s.get("prompt") or "")[:50]))

                earned = s.get("earned_score", 0)
                max_s = s.get("max_score") or s.get("point") or 1
                ev = s.get("evaluation") or {}
                passed = ev.get("passed", earned == max_s and max_s > 0)
                fails = [c.get("message", "") for c in ev.get("check_results", []) if not c.get("passed")]
                matrix.setdefault(key, {})[eid] = (passed, earned, max_s, "; ".join(fails))

    # 3. Print Compact Matrix Table
    col_w = 5
    hdrs = [f"{'Step':<10}"] + [f"{code:^{col_w}}" for code, _, _, _ in models] + [f"{'Pass':^6}"]
    sep = "-" * len(" | ".join(hdrs))

    current_tname = None
    universal_fails = []

    for (tname, sidx, sname), prompt_snip in steps_order:
        if tname != current_tname:
            current_tname = tname
            print(f"\n[{tname}]")
            print(" | ".join(hdrs))
            print(sep)

        row_res = matrix.get((tname, sidx, sname), {})
        passed_n = sum(1 for _, eid, _, _ in models if row_res.get(eid, (False,))[0])
        total_n = len(models)
        rate_str = f"{passed_n}/{total_n}"

        cols = [f"{sname:<10}"]
        for _, eid, _, _ in models:
            if eid not in row_res:
                cols.append(f"{'--':^{col_w}}")
            else:
                p, _, _, _ = row_res[eid]
                cell = "✓" if p else "✗"
                cols.append(f"{cell:^{col_w}}")
        cols.append(f"{rate_str:^6}")

        is_fail = (passed_n == 0)
        tag = " ◄ DEFECT (0%)" if is_fail else ""
        print(" | ".join(cols) + tag)

        if is_fail:
            universal_fails.append((tname, sname, prompt_snip, row_res))

    # 4. Universal Defect Diagnostics
    if universal_fails:
        print(f"\n🚨 {len(universal_fails)} UNIVERSALLY FAILING STEP(S):")
        for tname, sname, prompt_snip, row_res in universal_fails:
            print(f"\n  • [{tname}] {sname} (Prompt: \"{prompt_snip}...\")")
            for code, eid, lbl, _ in models:
                err = row_res.get(eid, (False, 0, 0, ""))[3]
                if err:
                    print(f"    - [{code}] {lbl}: {err}")
    print()


if __name__ == "__main__":
    main()
