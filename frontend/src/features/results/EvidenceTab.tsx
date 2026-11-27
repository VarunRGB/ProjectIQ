import { useMemo, useState } from 'react';
import type { EvidenceKind } from '../../api/types';
import { EvidenceCard } from '../../components/EvidenceCard';
import { Callout, Empty } from '../../components/ui';
import { EVIDENCE_KIND } from '../../lib/labels';
import type { ViewModel } from '../../lib/viewModel';

export default function EvidenceTab({ vm }: { vm: ViewModel }) {
  const items = vm.rec.evidence?.items ?? [];
  const [kind, setKind] = useState<EvidenceKind | 'ALL'>('ALL');
  const [eligible, setEligible] = useState<'ALL' | 'YES' | 'NO'>('ALL');
  const [cited, setCited] = useState(false);
  const [q, setQ] = useState('');
  const citedIds = useMemo(() => new Set(vm.assessment.criteria.flatMap((c) => c.evidence_ids)), [vm]);
  const kinds = [...new Set(items.map((e) => e.kind))];
  const list = items.filter((e) => (kind === 'ALL' || e.kind === kind) && (eligible === 'ALL' || (eligible === 'YES') === e.eligible_for_review)
    && (!cited || citedIds.has(e.id)) && (!q || (e.label + ' ' + e.text).toLowerCase().includes(q.toLowerCase())));
  if (!vm.rec.evidence) return <div className="panel"><Empty title="Evidence bundle not loaded">The backend did not return rubric/evidence.json for this evaluation.</Empty></div>;
  return (
    <section className="panel">
      <div className="panel-head">
        <div><h2>Evidence explorer</h2><p>Everything the rubric engine could see. Only eligible items can support a mark; eligibility means a reviewer may consider an item, not that its claim is true.</p></div>
      </div>
      <div className="row" style={{ alignItems: 'end' }}>
        <label className="f" style={{ flex: 1, minWidth: 200 }}>Search<input className="in" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Words in a label or excerpt" /></label>
        <label className="f">Kind<select className="in" value={kind} onChange={(e) => setKind(e.target.value as any)}><option value="ALL">All kinds</option>{kinds.map((k) => <option key={k} value={k}>{EVIDENCE_KIND[k].label} ({items.filter((e) => e.kind === k).length})</option>)}</select></label>
        <label className="f">Can support marks<select className="in" value={eligible} onChange={(e) => setEligible(e.target.value as any)}><option value="ALL">All</option><option value="YES">Eligible</option><option value="NO">Information only</option></select></label>
        <label className="check" style={{ paddingBottom: 8 }}><input type="checkbox" checked={cited} onChange={(e) => setCited(e.target.checked)} />Cited by a mark</label>
      </div>
      <p className="small muted mt">{list.length} of {items.length} items. Extraction: {vm.rec.evidence.report_extraction.toLowerCase()}.</p>
      {vm.rec.evidence.limitations.length > 0 && <Callout tone="muted"><ul style={{ margin: 0, paddingLeft: 18 }} className="small">{vm.rec.evidence.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul></Callout>}
      <div className="mt">{list.slice(0, 120).map((e) => <EvidenceCard key={e.id} e={e} />)}</div>
      {list.length > 120 && <p className="small muted mt">Showing the first 120. Narrow the filters to see the rest.</p>}
    </section>
  );
}
