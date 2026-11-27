import { useState } from 'react';
import type { Rubric } from '../../api/types';
import { Badge, Callout } from '../../components/ui';
import { humanize } from '../../lib/labels';
import { allTemplates, clone, deleteTemplate, effectiveShares, groupsOf, saveTemplate, validateRubric, type RubricTemplate } from '../../lib/rubric';
import RubricEditor from './RubricEditor';

export default function RubricLibrary() {
  const [list, setList] = useState<RubricTemplate[]>(allTemplates());
  const [editing, setEditing] = useState<{ base: RubricTemplate; draft: Rubric; name: string } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Rubric templates</h1><p>Reusable rubrics. Built-in templates come straight from the ProjectIQ engine and cannot be changed; copy one to make your own.</p></div>
      </div>
      {msg && <Callout tone="good">{msg}</Callout>}
      {editing ? (
        <section className="panel">
          <div className="panel-head">
            <label className="f" style={{ minWidth: 320 }}>Template name<input className="in" value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} /></label>
            <div className="row">
              <button className="btn" onClick={() => setEditing(null)}>Cancel</button>
              <button className="btn" onClick={() => setEditing({ ...editing, draft: clone(editing.base.rubric) })}>Restore original</button>
              <button className="btn primary" disabled={validateRubric(editing.draft).length > 0 || !editing.name.trim()} onClick={() => {
                const key = editing.base.origin === 'SAVED' ? editing.base.key : 'saved_' + Date.now();
                saveTemplate({ key, name: editing.name.trim(), summary: editing.base.origin === 'SAVED' ? editing.base.summary : `Based on ${editing.base.name}`, origin: 'SAVED', rubric: editing.draft });
                setList(allTemplates()); setEditing(null); setMsg(`Saved “${editing.name.trim()}”.`);
              }}>Save template</button>
            </div>
          </div>
          <RubricEditor rubric={editing.draft} onChange={(draft) => setEditing({ ...editing, draft })} />
        </section>
      ) : (
        <div className="grid g2">
          {list.map((t) => {
            const shares = effectiveShares(t.rubric);
            const groups = groupsOf(t.rubric);
            return (
              <section key={t.key} className="panel" style={{ marginTop: 0 }}>
                <div className="panel-head" style={{ marginBottom: 8 }}>
                  <div><h3>{t.name}</h3><p className="small">{t.summary}</p></div>
                  <Badge tone={t.origin === 'SAVED' ? 'info' : 'muted'}>{t.origin === 'SAVED' ? 'Yours' : t.origin === 'BACKEND' ? 'Engine' : 'Built in'}</Badge>
                </div>
                <div className="ledger-bar" style={{ height: 14 }} aria-hidden>
                  {groups.map((g, i) => {
                    const s = t.rubric.criteria.filter((c) => c.group === g).reduce((a, c) => a + (shares[c.id] || 0), 0);
                    return s > 0 ? <span key={g} className="lseg" style={{ flex: s, background: ['#1d2b53', '#3a4870', '#5b6a96', '#8592bb', '#b2bbd6', '#d5dbea'][i % 6] }} title={`${humanize(g)} ${Math.round(s * 100)}%`} /> : null;
                  })}
                </div>
                <div className="row tiny muted mt" style={{ gap: 12 }}>
                  {groups.map((g) => { const s = t.rubric.criteria.filter((c) => c.group === g).reduce((a, c) => a + (shares[c.id] || 0), 0); return s > 0 ? <span key={g}>{humanize(g)} {Math.round(s * 100)}%</span> : null; })}
                </div>
                <p className="small mt">{t.rubric.total_points} marks, {t.rubric.criteria.filter((c) => c.applicability !== 'EXCLUDED').length} of {t.rubric.criteria.length} criteria apply.</p>
                <div className="row">
                  <button className="btn sm" onClick={() => setEditing({ base: t, draft: clone(t.rubric), name: t.origin === 'SAVED' ? t.name : `${t.name} (copy)` })}>{t.origin === 'SAVED' ? 'Edit' : 'Copy and edit'}</button>
                  {t.origin === 'SAVED' && <button className="btn sm ghost danger" onClick={() => { if (confirm(`Delete “${t.name}”?`)) { deleteTemplate(t.key); setList(allTemplates()); } }}>Delete</button>}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
