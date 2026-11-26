import { Fragment } from 'react';
import type { AIAnalysis, AIFlaggedPassage } from '../../api/types';
import { AgentStatusBadge, Badge, Callout, Empty, Fact, HBar, More, Tip } from '../../components/ui';
import { INTEGRITY_CATEGORY, humanize } from '../../lib/labels';
import { pct, type ViewModel } from '../../lib/viewModel';
import { Gauge, Ring, toneForRisk } from '../../components/Visuals';

const AI_STATE: Record<AIAnalysis['status'], { label: string; tone: 'good' | 'warn' | 'bad' | 'muted' | 'info'; text: string }> = {
  COMPLETED: { label: 'Estimate available', tone: 'info', text: '' },
  NOT_AVAILABLE: { label: 'Not available', tone: 'muted', text: 'No AI estimate was produced, usually because the detector model is not installed. This is not 0%.' },
  INCONCLUSIVE: { label: 'Inconclusive', tone: 'warn', text: 'Too little of the student’s own writing could be scored for a meaningful estimate. This is not 0%.' },
  FAILED: { label: 'Failed', tone: 'bad', text: 'The AI estimate failed. Similarity results are unaffected. This is not 0%.' },
};

const SECONDARY: Record<string, { label: string; tone: 'warn' | 'muted' | 'info'; explain: string }> = {
  MUTUAL_ENTAILMENT_CANDIDATE: { label: 'Possible paraphrase (unconfirmed)', tone: 'warn', explain: 'Both sentences imply each other according to the inference model, but this pass is experimental. Read both before forming a view.' },
  NOT_MUTUALLY_ENTAILED: { label: 'Related, not a paraphrase', tone: 'muted', explain: 'Similar topic, but the sentences do not imply each other.' },
  NOT_REVIEWED_BUDGET: { label: 'Not reviewed (limit reached)', tone: 'muted', explain: 'Found by the loose search but beyond the number of candidates the inference model checks.' },
  MODEL_UNAVAILABLE: { label: 'Inference model unavailable', tone: 'muted', explain: 'The candidate could not be checked.' },
  REVIEW_UNAVAILABLE: { label: 'Review unavailable', tone: 'muted', explain: 'The secondary check failed; primary results are unaffected.' },
};

/**
 * Dedicated, extensible section for the Integrity agent's AI-generated-content
 * estimate, rendered strictly from the structured `ai_detection` (AIAnalysis)
 * contract. Unknown extra passage fields are shown under technical details so a
 * future detector version does not need UI changes to be inspectable.
 */
export function AIDetectionPanel({ ai }: { ai: AIAnalysis | null }) {
  const state = ai ? AI_STATE[ai.status] : AI_STATE.NOT_AVAILABLE;
  const passages = (ai?.flagged_passages ?? []) as AIFlaggedPassage[];
  return (
    <section className="panel" aria-labelledby="ai-h">
      <div className="panel-head">
        <div><h2 id="ai-h">AI-generated writing estimate</h2><p>How much of the student’s own writing reads as machine-generated. A reason for a conversation, never proof and never a deduction.</p></div>
        <Badge tone={state.tone}>{state.label}</Badge>
      </div>
      {ai?.status === 'COMPLETED' && ai.estimated_percent !== null ? (
        <div className="grid g2" style={{ alignItems: 'center' }}>
          <div className="row" style={{ gap: 20, flexWrap: 'nowrap' }}>
            <Gauge value={ai.estimated_percent} size={220} label="AI-like writing" caption="Advisory estimate" bands={[[15, 'good'], [35, 'warn'], [100, 'bad']]} />
            <div>
              <p style={{ margin: 0 }}>{ai.interpretation}</p>
              <p className="small muted" style={{ margin: '8px 0 0' }}>Confidence: <strong>{humanize(ai.confidence)}</strong>. Copied sentences are excluded before scoring.</p>
            </div>
          </div>
          <Callout tone="info" title="How to use this">Formal, formulaic or non-native writing can read as machine-like, and edited AI text can read as human. Discuss flagged passages with the student; do not deduct marks on this basis. ProjectIQ never feeds this estimate into the grade.</Callout>
        </div>
      ) : (
        <Callout tone={state.tone === 'bad' ? 'bad' : 'muted'} title="No estimate">{state.text}</Callout>
      )}
      {passages.length > 0 && (
        <div className="mt-l">
          <h3>Flagged passages ({passages.length})</h3>
          {passages.map((p, i) => {
            const known = ['location', 'words', 'score', 'threshold', 'detector', 'score_name', 'rule', 'stock_phrases', 'text'];
            const extra = Object.entries(p).filter(([k]) => !known.includes(k));
            return (
              <div key={i} className="ev mt">
                <div className="ev-head">
                  <span className="ev-title">{p.location ?? `Passage ${i + 1}`}{p.words ? <span className="muted small">, {p.words} words</span> : null}</span>
                  {p.score !== undefined && <Tip text={`${p.score_name ?? 'Score'} ${p.score} (flagged at ${p.rule ?? '>='} ${p.threshold}). The threshold is provisional until calibrated on real student writing.`}><Badge tone="pencil">{p.score_name ?? 'Score'} {p.score?.toFixed(3)}</Badge></Tip>}
                </div>
                {p.text && <div className="ev-text">{p.text}</div>}
                {!!p.stock_phrases?.length && <p className="small mt"><span className="muted">Stock phrases seen: </span>{p.stock_phrases.map((s) => <Badge key={s} tone="muted">{s}</Badge>)}</p>}
                {(p.detector || extra.length > 0) && (
                  <More summary="Technical details"><dl className="kv small">{p.detector && <><dt>Detector</dt><dd><code>{p.detector}</code></dd></>}{p.threshold !== undefined && <><dt>Threshold</dt><dd>{p.rule} {p.threshold}</dd></>}{extra.map(([k, v]) => <Fragment key={k}><dt>{humanize(k)}</dt><dd>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd></Fragment>)}</dl></More>
                )}
              </div>
            );
          })}
        </div>
      )}
      {(ai?.limitations?.length ?? 0) > 0 && <More summary="Limitations of this estimate"><ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>{ai!.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul></More>}
    </section>
  );
}

export default function IntegrityTab({ vm }: { vm: ViewModel }) {
  const i = vm.integrity;
  const card = vm.rec.result.agents.integrity;
  if (!i) return <div className="panel"><Empty title="Integrity did not run" /></div>;
  if (card.status === 'FAILED') {
    return (
      <div className="panel">
        <div className="panel-head"><h2>Integrity</h2><AgentStatusBadge status={card.status} /></div>
        <Callout tone="bad" title="The Integrity agent did not produce a result">
          {card.limitations.join(' ')} {card.error_code && <code>{card.error_code}</code>}
          <p className="mt">Similarity and the AI-writing estimate are unavailable for this evaluation, not zero. Integrity never affects marks, so grading is not blocked. Check the agent’s models on the evaluation machine (python scripts/setup_offline.py) and run a new evaluation if you need these indicators.</p>
        </Callout>
      </div>
    );
  }
  const cats = Object.entries(i.byCategory);
  const counted = cats.filter(([k]) => INTEGRITY_CATEGORY[k]?.counted);
  const advisory = cats.filter(([k]) => INTEGRITY_CATEGORY[k] && !INTEGRITY_CATEGORY[k].counted);
  const order = ['verbatim', 'near-verbatim', 'paraphrase', 'similar meaning (not confirmed)'];
  const matches = [...i.matches].sort((a, b) => order.indexOf(a.category) - order.indexOf(b.category));
  return (
    <>
      <section className="panel">
        <div className="panel-head">
          <div><h2>Similarity with supplied references</h2><p>Overlap is something to read, not a plagiarism verdict. Shared definitions, standard phrasing and attributed paraphrase are normal. Quoted text and the reference list are excluded.</p></div>
          <AgentStatusBadge status={card.status} />
        </div>
        {!i.comparisonAvailable ? (
          <Callout tone="muted" title="Similarity unavailable, not zero">No reference documents were uploaded for this submission, so there was nothing to compare against. Upload the papers the students cite to enable this check in a future evaluation.</Callout>
        ) : (
          <>
            <div className="grid g2" style={{ alignItems: 'center' }}>
              <div style={{ display: 'flex', justifyContent: 'center' }}>
                <Gauge value={i.overlap} size={220} label="Confirmed overlap" caption="Share of checked words" bands={[[10, 'good'], [25, 'warn'], [100, 'bad']]} />
              </div>
              <div className="vis-row">
                {(['verbatim', 'near-verbatim', 'paraphrase', 'similar meaning (not confirmed)'] as const).filter((k) => k in i.byCategory).map((k) => (
                  <Ring key={k} value={i.byCategory[k] ?? null} size={82} label={INTEGRITY_CATEGORY[k].label} sub={INTEGRITY_CATEGORY[k].counted ? 'Counted' : 'Advisory, not counted'} tone={INTEGRITY_CATEGORY[k].counted ? toneForRisk(i.byCategory[k] ?? null, 5, 15) : 'muted'} />
                ))}
              </div>
            </div>
            <div className="grid g3 mt-l">
              <Fact value={pct(i.inChunks)} label="In copied passages" tip="Overlap that forms runs of consecutive sentences following the same reference." />
              <Fact value={i.wordsChecked} label="Words checked" />
              <Fact value={i.references.length} label="References compared" scope={i.references.join(', ')} />
            </div>
            <div className="grid g2 mt-l">
              <div>
                <h4>By kind of overlap</h4>
                <div className="mt">{counted.map(([k, v]) => <HBar key={k} label={<Tip text={INTEGRITY_CATEGORY[k].explain}><span>{INTEGRITY_CATEGORY[k].label}</span></Tip>} value={v} max={Math.max(10, ...cats.map(([, x]) => x ?? 0))} tone={INTEGRITY_CATEGORY[k].tone === 'bad' ? 'bad' : 'warn'} display={pct(v)} />)}</div>
                {advisory.map(([k, v]) => <div key={k} className="mt"><HBar label={<Tip text={INTEGRITY_CATEGORY[k].explain}><span>{INTEGRITY_CATEGORY[k].label}</span></Tip>} value={v} max={Math.max(10, ...cats.map(([, x]) => x ?? 0))} tone="muted" display={pct(v)} /><p className="tiny muted" style={{ margin: 0 }}>Additional semantic matches; not counted in the overlap.</p></div>)}
              </div>
              <div>
                <h4>By reference</h4>
                <div className="mt">{Object.entries(i.byReference).map(([k, v]) => <HBar key={k} label={<span className="small" style={{ wordBreak: 'break-all' }}>{k}</span>} value={v} max={Math.max(10, ...Object.values(i.byReference))} tone="warn" display={pct(v)} />)}{!Object.keys(i.byReference).length && <p className="small muted">No confirmed overlap with any reference.</p>}</div>
              </div>
            </div>
          </>
        )}
      </section>

      {i.chunks.length > 0 && (
        <section className="panel">
          <div className="panel-head"><div><h2>Copied passages</h2><p>Consecutive sentences that follow one reference in order. Read both sides before drawing conclusions.</p></div></div>
          {i.chunks.map((c, k) => (
            <div key={k} className="mt">
              <div className="row small" style={{ marginBottom: 8 }}><strong>{c.sentences} sentences, {c.words} words</strong><span className="muted">from {c.reference}</span>{c.cited ? <Badge tone="good">Cited in the report</Badge> : <Badge tone="warn">No citation marker</Badge>}</div>
              <div className="pair">
                <div className="side"><div className="h">Student report, {c.document_location}</div>{c.document_text}</div>
                <div className="side"><div className="h">{c.reference}, {c.reference_location}</div>{c.reference_text}</div>
              </div>
            </div>
          ))}
        </section>
      )}

      {matches.length > 0 && (
        <section className="panel">
          <div className="panel-head"><div><h2>Matching sentences ({matches.length})</h2><p>Each sentence with the closest reference sentence. Scores show how the match was established.</p></div></div>
          {matches.map((m, k) => {
            const cat = INTEGRITY_CATEGORY[m.category];
            return (
              <div key={k} className="ev">
                <div className="ev-head">
                  <span className="row" style={{ gap: 6 }}><Tip text={cat?.explain ?? ''}><Badge tone={cat?.tone ?? 'muted'}>{cat?.label ?? m.category}</Badge></Tip>{!cat?.counted && <span className="tiny muted">Advisory, not counted</span>}</span>
                  <span className="row tiny muted" style={{ gap: 10 }}>{m.cited ? <Badge tone="good">Cited</Badge> : <Badge tone="muted">No citation marker</Badge>}
                    <Tip text="Shared word sequences."><span>Wording {Math.round(m.lexical * 100)}%</span></Tip>
                    <Tip text="Closeness of meaning from a sentence-embedding model."><span>Meaning {Math.round(m.semantic * 100)}%</span></Tip>
                    {m.entail_forward >= 0 && <Tip text="Probability that each sentence implies the other, from a natural-language-inference model. Both directions high means paraphrase."><span>Implies {Math.round(m.entail_forward * 100)}% / {Math.round(m.entail_backward * 100)}%</span></Tip>}
                  </span>
                </div>
                <div className="pair mt">
                  <div className="side"><div className="h">Student report, {m.location}</div>{m.text}</div>
                  <div className="side"><div className="h">{m.reference}, {m.reference_location}</div>{m.reference_text}</div>
                </div>
              </div>
            );
          })}
        </section>
      )}

      {i.secondary.length > 0 && (
        <section className="panel">
          <div className="panel-head">
            <div><h2>Additional semantic matches (advisory)</h2><p>Sentences that are only loosely similar to a reference, checked by a second, lower-threshold pass. They are never counted in the overlap, categories or copied passages, and the thresholds are not yet calibrated.</p></div>
            <Badge tone="muted">{i.secondary.length} not counted</Badge>
          </div>
          {i.secondary.map((m, k) => {
            const s = SECONDARY[m.secondary_review.status] ?? { label: humanize(m.secondary_review.status), tone: 'muted' as const, explain: '' };
            return (
              <div key={k} className="ev">
                <div className="ev-head">
                  <Tip text={s.explain}><Badge tone={s.tone}>{s.label}</Badge></Tip>
                  <span className="row tiny muted" style={{ gap: 10 }}>
                    <span>Meaning {Math.round(m.semantic * 100)}%</span>
                    {m.secondary_review.entail_forward != null && <span>Implies {Math.round(m.secondary_review.entail_forward * 100)}% / {Math.round(m.secondary_review.entail_backward * 100)}%</span>}
                    <Badge tone="muted">Not counted</Badge>
                  </span>
                </div>
                <div className="pair mt">
                  <div className="side"><div className="h">Student report, {m.location}</div>{m.text}</div>
                  <div className="side"><div className="h">{m.reference}, {m.reference_location}</div>{m.reference_text}</div>
                </div>
              </div>
            );
          })}
        </section>
      )}

      <AIDetectionPanel ai={i.ai} />

      <section className="panel">
        <h3>Method and limitations</h3>
        <dl className="kv small mt">{Object.entries(i.backends).map(([k, v]) => <Fragment key={k}><dt>{humanize(k)}</dt><dd><code>{v}</code></dd></Fragment>)}</dl>
        <ul className="small muted mt" style={{ paddingLeft: 18, marginBottom: 0 }}>{[...card.limitations, ...i.notes].map((n, k) => <li key={k}>{n}</li>)}</ul>
      </section>
    </>
  );
}
