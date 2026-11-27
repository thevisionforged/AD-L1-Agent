"""Evaluation harness: runs the golden set and reports accuracy, grounding, routing, latency and cost.

Usage (from the project root):
    python eval/run_eval.py --mode offline          # deterministic, no API key needed
    python eval/run_eval.py --mode online           # needs OPENAI_API_KEY in .env

Writes eval/results_<mode>.json and prints a Markdown table.
Built by Samarth Pandey (hack-grp-22).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import statistics
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from l1agent.agent import AgentService  # noqa: E402
from l1agent.config import Settings  # noqa: E402

# USD per 1M tokens for the default model (gpt-4.1-mini list price); used for an estimate only.
PRICE_IN, PRICE_OUT = 0.40, 1.60


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["offline", "online"], default="offline")
    args = ap.parse_args()
    base = Settings()
    if args.mode == "online" and not base.openai_api_key:
        sys.exit("OPENAI_API_KEY is not set; use --mode offline")
    s = dataclasses.replace(base, agent_mode="auto" if args.mode == "online" else "offline",
                            runtime_dir=Path(tempfile.mkdtemp(prefix="l1eval_")))
    svc = AgentService(s)
    cases = json.loads((ROOT / "eval" / "golden_set.json").read_text(encoding="utf-8"))["cases"]
    rows = []
    for c in cases:
        r = svc.handle(c["user"], c["message"])
        rep = r["reply"]
        cited = {x["source_id"].split("#")[0] for x in rep["citations"]}
        type_ok = rep["response_type"] == c["expected_type"]
        art_ok = None if "expected_article" not in c else c["expected_article"] in cited
        grp = (r["ticket_draft"] or {}).get("assignment_group")
        grp_ok = None if "expected_group" not in c else grp == c["expected_group"]
        ungrounded = [f for f in r["flags"] if f.startswith(("ungrounded", "invented"))]
        rows.append({"id": c["id"], "message": c["message"], "expected": c["expected_type"], "got": rep["response_type"],
                     "type_ok": type_ok, "article_ok": art_ok, "group_ok": grp_ok, "engine": r["engine"],
                     "cited": sorted(cited), "group": grp, "latency_ms": r["latency_ms"],
                     "prompt_tokens": r["usage"]["prompt_tokens"], "completion_tokens": r["usage"]["completion_tokens"],
                     "model_calls": r["usage"]["model_calls"], "flags": r["flags"], "ungrounded_flags": ungrounded})

    def rate(key):
        vals = [x[key] for x in rows if x[key] is not None]
        return sum(vals) / len(vals) if vals else 0.0, len(vals)

    pin = sum(x["prompt_tokens"] for x in rows)
    pout = sum(x["completion_tokens"] for x in rows)
    lat = [x["latency_ms"] for x in rows]
    summary = {
        "mode": args.mode, "model": s.model if args.mode == "online" else None, "cases": len(rows),
        "response_type_accuracy": rate("type_ok"), "citation_article_hit_rate": rate("article_ok"),
        "routing_accuracy": rate("group_ok"),
        "cases_with_ungrounded_output_corrected": sum(1 for x in rows if x["ungrounded_flags"]),
        "fallbacks": sum(1 for x in rows if x["engine"] == "offline-fallback"),
        "latency_ms_avg": int(statistics.mean(lat)), "latency_ms_p95": int(sorted(lat)[int(0.95 * (len(lat) - 1))]),
        "latency_ms_max": max(lat),
        "prompt_tokens": pin, "completion_tokens": pout,
        "est_cost_usd": round(pin / 1e6 * PRICE_IN + pout / 1e6 * PRICE_OUT, 4),
        "avg_model_calls_per_turn": round(statistics.mean(x["model_calls"] for x in rows), 2),
    }
    out = ROOT / "eval" / f"results_{args.mode}.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    print(f"| ID | Expected | Got | Type | Cited article | Routing | Latency ms | Tokens |\n|---|---|---|---|---|---|---|---|")
    for x in rows:
        f = lambda v: "-" if v is None else ("PASS" if v else "FAIL")  # noqa: E731
        print(f"| {x['id']} | {x['expected']} | {x['got']} | {f(x['type_ok'])} | {f(x['article_ok'])} | {f(x['group_ok'])} | "
              f"{x['latency_ms']} | {x['prompt_tokens'] + x['completion_tokens']} |")
    print("\n" + json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
