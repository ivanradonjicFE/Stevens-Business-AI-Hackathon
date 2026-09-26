"""Run the pipeline once: collect -> filter to semis -> historical analogs -> extrapolate -> alert.

    python run.py            # top 3 events
    python run.py --top 5
    python run.py --days 90  # demo: include hazards from the past 3 months
"""
import argparse
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import certifi

os.environ.setdefault("SSL_CERT_FILE", certifi.where())  # python.org macOS builds ship without CA certs

from alert import extrapolate, render  # noqa: E402
from collect import collect_all  # noqa: E402
from semis import filter_semis  # noqa: E402
from to_pdf import build as build_pdf  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--days", type=int, default=10, help="how far back to look for hazard events")
    args = ap.parse_args()

    print("Step 1: collecting global supply-chain events...")
    events = collect_all(args.days)
    supply = [e for e in events if e.kind == "NEWS" or e.alert in ("Orange", "Red")]
    print(f"  -> {len(events)} events total, {len(supply)} with supply-chain relevance (Orange/Red alerts + supply-chain news)")

    print("Step 2: filtering to semiconductor manufacturing & shipping...")
    relevant = filter_semis(events)
    for a in relevant:
        print(f"  {a.relevance:.2f}  sev={a.severity}  {a.category:20} {a.region:12} {a.event.title[:70]}")

    print("Steps 3-4: historical analogs + extrapolation...")
    top = [(a, extrapolate(a)) for a in relevant[:args.top]]

    print("Step 5: writing alert...\n")
    msg = render(top, len(supply), len(relevant))
    out = Path(__file__).parent / "out"
    out.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    md = out / f"alert-{stamp}.md"
    md.write_text(msg)
    audit = [{"assessment": asdict(a), "extrapolation": x} for a, x in top]
    (out / f"audit-{stamp}.json").write_text(json.dumps(audit, indent=2, default=str))
    print(msg)
    pdf = build_pdf(md)
    print(f"\nSaved out/alert-{stamp}.md, out/{pdf.name} and out/audit-{stamp}.json")


if __name__ == "__main__":
    main()
