import { useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { backend, type BackendCapabilities } from '../../api';
import type { EvaluationRequest, ExecutionerMode, Rubric } from '../../api/types';
import { Badge, Callout, More, Tip } from '../../components/ui';
import { humanize } from '../../lib/labels';
import { evaluationModeLabel } from '../../lib/facultyExperience';
import { allTemplates, clone, defaultRubric, DEFAULT_TEMPLATE_KEY, ID_PATTERN, isResearch, researchNeeded, saveTemplate, setResearch, validateRubric } from '../../lib/rubric';
import RubricEditor from './RubricEditor';

// From projectiq_executioner/orchestration/contracts.py SUITE_CATALOGS (readiness is selected via run_readiness).
const SUITES: Record<string, { catalogs: string[]; scope: string; cost: number }> = {
  api: { catalogs: ['oracle'], scope: 'Reviewed API requests and assertions', cost: 1 },
  auth: { catalogs: ['auth', 'oracle'], scope: 'Authentication and session checks', cost: 1 },
  db_fixture: { catalogs: ['fixture'], scope: 'Disposable database setup', cost: 1 },
  db_postconditions: { catalogs: ['auth', 'oracle', 'postconditions'], scope: 'Read-only database state after actions', cost: 1 },
  scenario_isolation: { catalogs: ['auth', 'oracle', 'postconditions', 'isolation'], scope: 'Two independent VM scenarios', cost: 2 },
  dependent_workflow: { catalogs: ['fixture', 'workflow'], scope: 'Dependent API workflow', cost: 1 },
  crud: { catalogs: ['auth', 'oracle', 'crud'], scope: 'Authenticated create, read, update, delete', cost: 1 },
  browser_ui: { catalogs: ['browser'], scope: 'Real-browser DOM checks', cost: 1 },
  browser_login: { catalogs: ['login'], scope: 'Browser login and session', cost: 1 },
};
const REPO = /^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+?(?:\.git)?\/?$/;
const SHA = /^[0-9a-f]{40}$/;

type Step = 0 | 1 | 2 | 3;
const STEPS = ['Submission', 'Rubric', 'Agents and modes', 'Review and start'];

export default function NewEvaluation({ caps }: { caps: BackendCapabilities | null }) {
  const nav = useNavigate();
  const [step, setStep] = useState<Step>(0);
  const [title, setTitle] = useState('');
  const [sid, setSid] = useState('');
  const [students, setStudents] = useState('');
  const [course, setCourse] = useState('');
  const [report, setReport] = useState<File | null>(null);
  const [hasRepo, setHasRepo] = useState(true);
  const [repo, setRepo] = useState('');
  const [ref, setRef] = useState('');
  const [refs, setRefs] = useState<File[]>([]);
  const [templateKey, setTemplateKey] = useState(DEFAULT_TEMPLATE_KEY);
  const [rubric, setRubric] = useState<Rubric>(defaultRubric());
  const [verify, setVerify] = useState(false);
  const [mode, setMode] = useState<ExecutionerMode>('STATIC');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [trustDemo, setTrustDemo] = useState(false);
  const [disposable, setDisposable] = useState(false);
  const [readiness, setReadiness] = useState<'AUTO' | 'YES' | 'NO'>('AUTO');
  const [vmBudget, setVmBudget] = useState(2);
  const [suites, setSuites] = useState<Record<string, Record<string, string>>>({});
  const [resumeTarget, setResumeTarget] = useState<'STATIC' | 'REVIEWED'>('REVIEWED');
  const [resumeFrom, setResumeFrom] = useState('');
  const [importResult, setImportResult] = useState<File | null>(null);
  const [resumeFile, setResumeFile] = useState<File | null>(null);
  const [ack, setAck] = useState(false);
  const [rubricMode, setRubricMode] = useState<EvaluationRequest['rubric_mode']>('AUTOMATIC');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);
  const reportInput = useRef<HTMLInputElement>(null);
  const refsInput = useRef<HTMLInputElement>(null);
  const templates = useMemo(() => allTemplates(), [savedMsg]);

  const research = researchNeeded(rubric);
  const hasResearchCrit = rubric.criteria.some(isResearch);
  const rubricIssues = validateRubric(rubric);
  const liveModes = caps?.liveExecutionerModes ?? ['STATIC'];
  const modeLive = liveModes.includes(mode);
  const demo = mode === 'TRUSTED_DEMO';
  const advancedMode = mode === 'REVIEWED' || mode === 'RESUME';
  const modeChoices: ExecutionerMode[] = ['STATIC', ...(liveModes.includes('TRUSTED_DEMO') ? ['TRUSTED_DEMO' as const] : []), ...(showAdvanced || advancedMode ? ['REVIEWED' as const,'RESUME' as const] : [])];
  const composed = hasRepo && !demo && (mode !== 'STATIC' || !!importResult);
  const suiteCost = Object.keys(suites).reduce((a, k) => a + SUITES[k].cost, 0);
  const vmCost = suiteCost + (readiness === 'YES' || (readiness === 'AUTO' && suiteCost === 0) ? 1 : 0);
  const codeCriteria = rubric.criteria.filter((c) => c.applicability !== 'EXCLUDED' && c.required_evidence.some((k) => k === 'STATIC_SOURCE' || k === 'TEST_OBSERVATION'));

  const problems: Record<Step, string[]> = {
    0: [
      !title.trim() && 'Enter the project title.',
      !ID_PATTERN.test(sid) && 'Submission ID: letters, digits, - or _, starting with a letter or digit.',
      !report && 'Attach the project report (PDF, DOCX, TXT or MD).',
      hasRepo && !REPO.test(repo.trim()) && 'Repository must be a public https://github.com/owner/repo address.',
      hasRepo && ref && !/^[A-Za-z0-9][A-Za-z0-9_./-]{0,150}$/.test(ref) && 'The branch, tag or commit contains characters that are not allowed.',
    ].filter(Boolean) as string[],
    1: rubricIssues.map((i) => i.message),
    2: [
      hasRepo && (mode !== 'STATIC' || importResult) && !SHA.test(ref) && `${importResult && mode === 'STATIC' ? 'Imported Executioner evidence' : mode} needs the exact 40-character commit SHA in step 1.`,
      hasRepo && mode === 'REVIEWED' && !Object.keys(suites).length && readiness === 'NO' && 'Select at least one reviewed suite, or allow the readiness check.',
      hasRepo && mode === 'REVIEWED' && Object.entries(suites).some(([, c]) => Object.values(c).some((p) => !/^[A-Za-z0-9_./-]{1,230}$/.test(p) || p.startsWith('/') || p.split('/').includes('..'))) && 'Catalog paths must be relative paths inside the trusted review root.',
      hasRepo && mode === 'REVIEWED' && !importResult && vmCost > vmBudget && `The selected suites need ${vmCost} VM launches; raise the budget.`,
      hasRepo && mode === 'RESUME' && modeLive && !resumeFrom.trim() && !resumeFile && !importResult && 'RESUME needs the earlier executioner_result.json.',
      hasRepo && demo && !trustDemo && 'Confirm operator trust for this exact repository commit.',
      hasRepo && demo && !modeLive && 'Automatic evaluation must be enabled by the local operator.',
      hasRepo && !demo && !modeLive && !importResult && `${mode} needs an operator VM configuration on the evaluation machine. Attach an existing Executioner result to import, or choose STATIC.`,
      composed && !ack && 'Confirm that the Executioner evidence belongs to this submission and commit.',
    ].filter(Boolean) as string[],
    3: [],
  };
  const allProblems = [...problems[0], ...problems[1], ...problems[2]];

  const applyTemplate = (key: string) => {
    const t = templates.find((x) => x.key === key);
    if (!t) return;
    setTemplateKey(key); setRubric(clone(t.rubric));
    if (key === 'report_only') { setHasRepo(false); setTrustDemo(false); setDisposable(false); setAck(false); }
  };

  const start = async () => {
    if (busy || allProblems.length) return;
    setBusy(true); setErr(null);
    try {
      const req: EvaluationRequest = {
        title: title.trim(), submission_id: sid.trim(), students: students.split(',').map((s) => s.trim()).filter(Boolean), course: course.trim() || undefined,
        report_file_name: report!.name, report_file: report, repository_url: hasRepo ? repo.trim().replace(/\/$/, '') : null, ref: hasRepo && ref ? ref.trim() : null,
        reference_files: refs.map((f) => f.name), reference_file_objects: refs, executioner_import_file: hasRepo && !demo ? importResult : null,
        resume_from_file: hasRepo && mode === 'RESUME' ? resumeFile : null, acknowledge_imported: composed && ack, rubric, research_required: research, verify_research: research && verify,
        executioner: hasRepo ? {
          mode, run_readiness: demo ? 'AUTO' : readiness, max_vm_launches: demo ? 1 : mode === 'STATIC' ? 0 : vmBudget,
          ...(demo ? { trust_demo_source: trustDemo, confirm_disposable_state: disposable } : {}),
          suites: mode === 'REVIEWED' ? Object.entries(suites).map(([kind, catalogs]) => ({ kind, catalogs })) : [],
          ...(mode === 'RESUME' ? { resume_target: resumeTarget, resume_from: resumeFrom || importResult?.name } : {}),
        } : null,
        rubric_mode: rubricMode,
      };
      const { run_id } = await backend.startEvaluation(req);
      nav(`/runs/${run_id}`);
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };

  const canVisit = (i: number) => !busy && (i <= step || ([0,1,2] as const).filter(j=>j<i).every(j=>!problems[j].length));
  const goTo = (i: Step) => { if (!canVisit(i)) return; setStep(i); setErr(null); window.scrollTo({top:0}); };
  const next = () => goTo(Math.min(3, step + 1) as Step);
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>New evaluation</h1><p>Set up one submission: the report, the optional repository, the rubric and how each agent runs.</p></div>
      </div>
      {caps?.kind === 'mock' && <div className="banner"><strong>Demo backend.</strong> Starting an evaluation replays a matching generated result. Your files are not uploaded or analysed.</div>}
      <div className="steps" role="tablist">
        {STEPS.map((s, i) => (
          <button key={s} role="tab" aria-selected={step === i} className={step === i ? 'on' : i < step ? 'done' : ''} disabled={!canVisit(i)} onClick={() => goTo(i as Step)}>
            <span className="n">{i + 1}</span>{s}{i < 3 && problems[i as Step].length > 0 && i < step && <Badge tone="warn">{problems[i as Step].length}</Badge>}
          </button>
        ))}
      </div>

      {step === 0 && (
        <section className="panel">
          <div className="grid g2">
            <label className="f">Project title<input className="in" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="For example: CampusConnect club portal" /></label>
            <label className="f">Submission ID<span className="hint">Used in file names and saved decisions</span><input className="in" value={sid} onChange={(e) => setSid(e.target.value)} placeholder="CE3102-GROUP-07" /></label>
            <label className="f">Students<span className="hint">Separate names with commas</span><input className="in" value={students} onChange={(e) => setStudents(e.target.value)} /></label>
            <label className="f">Course<input className="in" value={course} onChange={(e) => setCourse(e.target.value)} placeholder="Optional" /></label>
          </div>
          <div className="grid g2 mt-l">
            <div>
              <h4>Project report</h4>
              <input ref={reportInput} type="file" accept=".pdf,.docx,.txt,.md" hidden onChange={(e) => setReport(e.target.files?.[0] ?? null)} />
              <div className="drop mt" role="button" tabIndex={0} onClick={() => reportInput.current?.click()} onKeyDown={(e) => e.key === 'Enter' && reportInput.current?.click()}>
                {report ? <span className="file-chip">{report.name} <span className="muted">{Math.round(report.size / 1024)} KB</span></span> : <>Choose the report file<br /><span className="tiny">PDF, DOCX, TXT or Markdown. Scanned pages without text cannot be read.</span></>}
              </div>
            </div>
            <div>
              <h4>Reference documents for Integrity <span className="muted small">(optional)</span></h4>
              <input ref={refsInput} type="file" multiple accept=".pdf,.docx,.txt,.md" hidden onChange={(e) => setRefs([...refs, ...Array.from(e.target.files ?? [])])} />
              <div className="drop mt" role="button" tabIndex={0} onClick={() => refsInput.current?.click()} onKeyDown={(e) => e.key === 'Enter' && refsInput.current?.click()}>
                Add the papers the students cite<br /><span className="tiny">Without them, similarity is reported as unavailable, not as 0%.</span>
              </div>
              <div className="row mt" style={{ gap: 6 }}>{refs.map((f, i) => <span key={i} className="file-chip">{f.name}<button className="btn ghost sm" aria-label={`Remove ${f.name}`} onClick={() => setRefs(refs.filter((_, j) => j !== i))}>×</button></span>)}</div>
            </div>
          </div>
          <hr className="rule" />
          <div className="choice-cards g2 grid">
            <label className={`choice ${hasRepo ? 'on' : ''}`}><input type="radio" checked={hasRepo} onChange={() => setHasRepo(true)} /><div><strong>Report and GitHub repository</strong><div className="small muted">The Executioner inspects the code and can test it.</div></div></label>
            <label className={`choice ${!hasRepo ? 'on' : ''}`}><input type="radio" checked={!hasRepo} onChange={() => { setHasRepo(false); setTrustDemo(false); setDisposable(false); setAck(false); }} /><div><strong>Report only</strong><div className="small muted">No repository. Code and testing criteria should be marked not applicable.</div></div></label>
          </div>
          {hasRepo && (
            <div className="grid g2 mt">
              <label className="f">Repository URL<input className="in" value={repo} onChange={(e) => { setRepo(e.target.value); setTrustDemo(false); setDisposable(false); setAck(false); }} placeholder="https://github.com/owner/repo" /></label>
              <label className="f">Branch, tag or commit<span className="hint">Optional for STATIC. Runtime and imported evidence need the exact 40-character commit SHA.</span><input className="in" value={ref} onChange={(e) => { setRef(e.target.value); setTrustDemo(false); setDisposable(false); setAck(false); }} placeholder="main or a commit SHA" /></label>
            </div>
          )}
          {!hasRepo && codeCriteria.length > 0 && <div className="mt"><Callout tone="warn" title="The rubric expects source code or test results">{codeCriteria.map((c) => c.title).join(', ')} require evidence that a report-only submission cannot provide. They would stay unassessed. Use the “Report-only submission” template, or mark them not applicable in step 2.<div className="mt"><button className="btn sm" onClick={() => { applyTemplate('report_only'); setStep(1); }}>Use the report-only template</button></div></Callout></div>}
        </section>
      )}

      {step === 1 && (
        <section className="panel">
          <div className="panel-head">
            <div><h2>Rubric</h2><p>Start from a template, then adjust marks, weights, levels and which evidence each criterion may use. Weights can also be changed after evaluation without rerunning agents.</p></div>
            <div className="row">
              <select className="in" style={{ width: 280 }} value={templateKey} onChange={(e) => applyTemplate(e.target.value)} aria-label="Template">
                {templates.map((t) => <option key={t.key} value={t.key}>{t.name}{t.origin === 'SAVED' ? ' (saved)' : ''}</option>)}
              </select>
              <button className="btn" onClick={() => { applyTemplate(DEFAULT_TEMPLATE_KEY); }}>Restore default</button>
              <button className="btn" disabled={rubricIssues.length > 0} onClick={() => {
                const name = prompt('Name for this template', rubric.title);
                if (!name) return;
                const key = 'saved_' + Date.now();
                saveTemplate({ key, name, summary: `Saved from ${rubric.title}`, origin: 'SAVED', rubric: clone(rubric) });
                setTemplateKey(key); setSavedMsg(`Saved “${name}” to your templates.`);
              }}>Save as template</button>
            </div>
          </div>
          {savedMsg && <Callout tone="good">{savedMsg}</Callout>}
          <div className="mt"><RubricEditor rubric={rubric} onChange={setRubric} /></div>
        </section>
      )}

      {step === 2 && (
        <>
          <section className="panel">
            <h2>Agents</h2>
            <div className="stack mt">
              <label className="check"><input type="checkbox" checked disabled /><span><strong>Report summary</strong> <Badge tone="muted">Always included</Badge><br /><span className="small muted">Exact key sentences from the report for orientation. Used for orientation, never used for marks.</span></span></label>
              <label className="check"><input type="checkbox" checked disabled /><span><strong>Integrity</strong> <Badge tone="muted">Always included</Badge><br /><span className="small muted">{refs.length ? `Compares the report with ${refs.length} reference document(s)` : 'No references uploaded: similarity will be unavailable'} and estimates AI-like writing. Advisory only.{caps?.aiDetection === 'NOT_INSTALLED' && ' The AI detector model is not installed on the evaluation machine; run scripts/setup_offline.py.'}</span></span></label>
              <label className="check">
                <input type="checkbox" checked={research} disabled={!hasResearchCrit} onChange={(e) => { try { setRubric(setResearch(rubric, e.target.checked)); } catch (x: any) { setErr(humanize(x.message)); } }} />
                <span><strong>Research and citations</strong><br /><span className="small muted">{hasResearchCrit ? 'Linked to the rubric’s research criterion. Off: excluded from grading and its weight is shared among the other criteria.' : 'This rubric has no research criterion.'}</span></span>
              </label>
              <label className="check" style={{ marginLeft: 26, opacity: research ? 1 : 0.5 }}>
                <input type="checkbox" checked={verify && research} disabled={!research || caps?.onlineResearchVerification === false} onChange={(e) => setVerify(e.target.checked)} />
                <span><strong>Verify papers online</strong> <Badge tone="muted">Opt-in</Badge><br /><span className="small muted">Sends bibliography titles and DOIs to academic metadata services (for example OpenAlex or Crossref). Leave off if you have no permission to use them. Without it, identities stay unchecked.</span></span>
              </label>
            </div>
          </section>

          <section className="panel">
            <div className="panel-head"><div><h2>Executioner mode</h2><p>{hasRepo ? 'How far the repository evaluation may go. Runtime execution is local, disposable and requires operator trust or approved reviewed suites.' : 'Report-only submission: the Executioner does not run.'}</p></div></div>
            {hasRepo && (
              <>
                <div className="row between"><span className="small muted">Choose how to inspect the project. No code is executed by default.</span><button className="btn sm" type="button" disabled={advancedMode} aria-expanded={showAdvanced || advancedMode} onClick={()=>setShowAdvanced(v=>!v)}>{showAdvanced || advancedMode ? 'Hide advanced modes' : 'Show advanced modes'}</button></div>
                <div className="grid g2 choice-cards mt">
                  {modeChoices.map((m) => (
                    <label key={m} className={`choice ${mode === m ? 'on' : ''}`}>
                      <input type="radio" checked={mode === m} onChange={() => { setMode(m); setTrustDemo(false); setDisposable(false); setAck(false); if (m === 'TRUSTED_DEMO') setImportResult(null); }} />
                      <div>
                        <div className="row between"><strong>{evaluationModeLabel(m)}</strong>{liveModes.includes(m) ? <Badge tone="good">Available</Badge> : <Tip text="The Executioner supports this mode on its own, but the unified evaluator does not launch it yet. You can import an existing result."><Badge tone="warn">Import only</Badge></Tip>}</div>
                        <div className="small muted">{m === 'STATIC' ? 'Inspect the pinned code, plan tests, analyse quality. Never runs student code. No VM.' : m === 'TRUSTED_DEMO' ? 'Trusted local demo: automatically prepare and test supported applications with at most one VM. No catalogs.' : m === 'REVIEWED' ? 'Also run operator-approved suites in disposable VMs for an exact commit.' : 'Re-verify an earlier run’s receipts and reuse its evidence; optionally run only new approved work.'}</div>
                      </div>
                    </label>
                  ))}
                </div>
                {mode === 'STATIC' && <div className="mt"><Callout tone="info">Planned tests will be listed but not executed, so functional-behaviour marks will need verified runs or your own judgment.</Callout></div>}
                {demo && <div className="stack mt-l">
                  <Callout tone="warn" title="Trusted local operator only">The automatic path is not security-attested for arbitrary student code. Unsupported profiles may return static or readiness-only evidence. Generated observations do not automatically earn reviewed marks.</Callout>
                  <label className="check"><input type="checkbox" checked={trustDemo} onChange={(e) => setTrustDemo(e.target.checked)} /><span>I am the local operator and explicitly trust this exact repository commit for a disposable demo.</span></label>
                  <label className="check"><input type="checkbox" checked={disposable} onChange={(e) => setDisposable(e.target.checked)} /><span>Allow supported adaptive CRUD mutations of synthetic disposable state (optional).</span></label>
                </div>}
                {mode === 'REVIEWED' && (
                  <div className="stack mt-l">
                    <div className="grid g3">
                      <label className="f">Readiness check<select className="in" value={readiness} onChange={(e) => setReadiness(e.target.value as any)}><option value="AUTO">Automatic</option><option value="YES">Always</option><option value="NO">Skip</option></select></label>
                      <label className="f">VM launch budget<span className="hint">0 to 12. Scenario isolation uses 2.</span><input className="in" type="number" min={0} max={12} value={vmBudget} onChange={(e) => setVmBudget(Math.max(0, Math.min(12, Number(e.target.value) || 0)))} /></label>
                      <div className="sub small">Estimated maximum including readiness: <strong>{vmCost}</strong> launch{vmCost === 1 ? '' : 'es'}.</div>
                    </div>
                    <div className="table-wrap"><table className="t">
                      <thead><tr><th>Suite</th><th>What it checks</th><th>Reviewed catalogs (relative paths)</th></tr></thead>
                      <tbody>{Object.entries(SUITES).map(([k, s]) => (
                        <tr key={k}>
                          <td><label className="check"><input type="checkbox" checked={!!suites[k]} onChange={(e) => { const n = { ...suites }; if (e.target.checked) n[k] = Object.fromEntries(s.catalogs.map((c) => [c, `${k}_${c}.json`])); else delete n[k]; setSuites(n); }} /><strong>{humanize(k)}</strong></label></td>
                          <td className="small">{s.scope}</td>
                          <td>{suites[k] ? <div className="stack" style={{ gap: 4 }}>{s.catalogs.map((c) => <label key={c} className="row small" style={{ flexWrap: 'nowrap' }}><span style={{ width: 100 }} className="muted">{c}</span><input className="in" value={suites[k][c]} onChange={(e) => setSuites({ ...suites, [k]: { ...suites[k], [c]: e.target.value } })} /></label>)}</div> : <span className="tiny muted">{s.catalogs.join(', ')}</span>}</td>
                        </tr>
                      ))}</tbody>
                    </table></div>
                    <p className="tiny muted">Catalogs must be approved by an operator for this exact commit. Selecting a suite here does not authorise execution; the Executioner re-checks every approval.</p>
                  </div>
                )}
                {mode === 'RESUME' && (
                  <div className="grid g3 mt-l">
                    <label className="f">Resume as<select className="in" value={resumeTarget} onChange={(e) => setResumeTarget(e.target.value as any)}><option value="REVIEWED">Reviewed</option><option value="STATIC">Static</option></select></label>
                    <div className="stack" style={{ gap: 6 }}>
                      <label className="f">Earlier result path<span className="hint">executioner_result.json on the evaluation machine (its receipts are re-verified)</span><input className="in" value={resumeFrom} onChange={(e) => { setResumeFrom(e.target.value); setAck(false); }} placeholder="outputs/executioner/…/executioner_result.json" /></label>
                      <span className="tiny muted">or upload it: <input type="file" accept=".json" aria-label="Earlier Executioner result" onChange={(e) => { setResumeFile(e.target.files?.[0] ?? null); setAck(false); }} /></span>
                    </div>
                    <label className="f">New VM launches allowed<span className="hint">0 forbids any new VM</span><input className="in" type="number" min={0} max={12} value={vmBudget} onChange={(e) => setVmBudget(Math.max(0, Math.min(12, Number(e.target.value) || 0)))} /></label>
                  </div>
                )}
                {!demo && mode !== 'STATIC' && modeLive && (
                  <div className="mt-l">
                    <Callout tone="info" title={`How ${mode} runs here`}>
                      The Executioner runs on its own with the operator’s VM configuration. The other agents then run live, and all results are bundled and re-verified as one imported evaluation. The results are labelled as imported evidence with your acknowledgment.
                    </Callout>
                  </div>
                )}
                {!modeLive && (
                  <div className="mt-l">
                    <Callout tone="warn" title={`${mode} cannot run on this evaluation machine yet`}>
                      Running {mode} needs an operator VM configuration (start the bridge with --vm-config). You can still use {mode} evidence by attaching a result from a separate Executioner run; it is imported and re-verified against its receipts.
                    </Callout>
                  </div>
                )}
                {!demo && <details className="sub mt" open={importResult ? true : undefined}>
                  <summary>Import existing Executioner evidence <span className="muted small">(optional, advanced)</span></summary>
                  <p className="small muted" style={{ margin: '4px 0 8px' }}>Use evidence already produced by a separate STATIC, REVIEWED or RESUME run for the same commit. The commit must match the SHA above.</p>
                  <input type="file" accept=".json" onChange={(e) => { setImportResult(e.target.files?.[0] ?? null); setAck(false); }} aria-label="Executioner result to import" />
                </details>}
                {composed && (
                  <label className="check mt"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /><span>I confirm the Executioner evidence belongs to this submission and commit. <span className="muted">Recorded as an operator acknowledgment, not independent authentication. Without it, imported evidence cannot support marks.</span></span></label>
                )}
              </>
            )}
          </section>

          <section className="panel">
            <h2>Rubric model</h2>
            <div className="grid g3 choice-cards mt">
              {([['AUTOMATIC', 'Propose marks', 'The local rubric model proposes levels with cited evidence. You approve them.'], ['PREVIEW', 'Preview only', 'Collect evidence without any rubric-model call. You score every criterion.'], ['OBJECTIVE_ONLY', 'Objective only', 'Calculate only test-based criteria; leave judgments to you.']] as const).map(([k, t, d]) => (
                <label key={k} className={`choice ${rubricMode === k ? 'on' : ''}`}><input type="radio" checked={rubricMode === k} onChange={() => setRubricMode(k)} /><div><strong>{t}</strong><div className="small muted">{d}</div></div></label>
              ))}
            </div>
          </section>
        </>
      )}

      {step === 3 && (
        <section className="panel">
          <h2>Ready to start</h2>
          <div className="grid g2 mt">
            <dl className="kv">
              <dt>Project</dt><dd>{title || <span className="c-warn">Missing</span>} <span className="muted">({sid || 'no ID'})</span></dd>
              <dt>Report</dt><dd>{report?.name ?? <span className="c-warn">Missing</span>}</dd>
              <dt>Repository</dt><dd>{hasRepo ? <>{repo || <span className="c-warn">Missing</span>} {ref && <code>{ref.slice(0, 12)}</code>}</> : 'Report only'}</dd>
              <dt>References</dt><dd>{refs.length ? `${refs.length} document(s)` : 'None (similarity unavailable)'}</dd>
              <dt>Rubric</dt><dd>{rubric.title}, {rubric.total_points} marks, {rubric.criteria.filter((c) => c.applicability !== 'EXCLUDED').length} criteria apply</dd>
            </dl>
            <div>
              <h4>What will run</h4>
              <ul className="small" style={{ paddingLeft: 18 }}>
                <li>Report summary (always)</li>
                <li>Integrity: AI-like writing estimate{refs.length ? ' and similarity' : ''}</li>
                <li>{research ? `Research${verify ? ' with online verification' : ' without online verification'}` : 'Research skipped (not in the rubric)'}</li>
                <li>{hasRepo ? (demo ? 'Automatic evaluation (trusted local demo, at most one VM)' : importResult ? `Executioner result imported from ${importResult.name}` : `Executioner in ${mode} mode`) : 'Executioner skipped (report only)'}{composed && ', bundled as acknowledged imported evidence'}</li>
                <li>Rubric: {rubricMode === 'AUTOMATIC' ? 'AI proposals for your approval' : rubricMode === 'PREVIEW' ? 'no model calls' : 'objective criteria only'}</li>
              </ul>
            </div>
          </div>
          {allProblems.length > 0 && <Callout tone="warn" title="Fix these first"><ul style={{ margin: 0, paddingLeft: 18 }}>{allProblems.map((p, i) => <li key={i}>{p}</li>)}</ul></Callout>}
          {err && <div className="mt"><Callout tone="bad">{err}</Callout></div>}
          <More summary="Privacy"><p className="small muted">Reports, findings and saved outputs contain confidential student work. They stay on the evaluation machine. Repository acquisition may contact GitHub. Optional online research verification sends bibliography metadata to external academic services. Do not submit confidential repositories or documents without permission.</p></More>
        </section>
      )}

      <div className="row between mt-l">
        <button className="btn" disabled={step === 0 || busy} onClick={() => goTo(Math.max(0, step - 1) as Step)}>Back</button>
        <div className="row">
          {step < 3 && problems[step].length > 0 && <span className="small c-warn">{problems[step][0]}</span>}
          {step < 3 ? <button className="btn primary" disabled={busy || problems[step].length > 0} onClick={next}>Continue</button> : <button className="btn primary" disabled={busy || allProblems.length > 0} onClick={start}>{busy ? 'Starting…' : 'Start evaluation'}</button>}
        </div>
      </div>
    </div>
  );
}
