"""Inspect Phase 3 v1.4 JSON evidence diagnostics without model downloads.

Usage:
  python tools/review_phase3_v14.py outputs/report_analysis.json
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description='Review ProjectIQ Phase 3 classifications and evidence')
    parser.add_argument('json_path', type=Path, help='JSON written by test_research_document.py')
    args = parser.parse_args()
    with args.json_path.open(encoding='utf-8') as f:
        data = json.load(f)
    records = data.get('relevance_results') or []
    if not records:
        raise SystemExit('No relevance_results found; run analyze_research_document first.')
    print(f'References: {len(records)}')
    print('Labels:', dict(sorted(Counter(r['classification'] for r in records).items())))
    print('Evidence strength:', dict(sorted(Counter(r.get('method_evidence_strength','missing') for r in records).items())))
    print('Identity evidence:', dict(sorted(Counter(r.get('evidence_reliability','missing') for r in records).items())))
    print()
    for r in records:
        i=r.get('reference_index',0)+1
        label=r.get('classification','?')
        confidence=r.get('confidence','?')
        strength=r.get('method_evidence_strength','?')
        reliability=r.get('evidence_reliability','?')
        print(f'[{i:02}] {label:26} confidence={confidence:6} method={strength:8} paper={reliability}')
        if r.get('method_evidence_traces'):
            for trace in r['method_evidence_traces'][:2]:
                print('       evidence:', trace[:145])
        if r.get('verification_status') not in ('verified','needs_review'):
            print('       warning: paper identity is not verified')


if __name__ == '__main__':
    main()
