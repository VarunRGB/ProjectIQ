import { useEffect, useState } from 'react';
import { NavLink, Route, Routes, useLocation } from 'react-router-dom';
import { backend, type BackendCapabilities } from './api';
import EvaluationsList from './features/evaluations/EvaluationsList';
import NewEvaluation from './features/configure/NewEvaluation';
import RubricLibrary from './features/configure/RubricLibrary';
import RunProgress from './features/run/RunProgress';
import ResultsPage from './features/results/ResultsPage';
import ReportPage from './features/report/ReportPage';

function Mark() {
  return (
    <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="7" fill="#2c3d6e" />
      <path d="M9 23V9h7.2a4.6 4.6 0 0 1 0 9.2H9" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinejoin="round" />
      <circle cx="23.2" cy="22.6" r="2.6" fill="#33b39b" />
    </svg>
  );
}

export default function App() {
  const loc = useLocation();
  const [caps, setCaps] = useState<BackendCapabilities | null>(null);
  useEffect(() => { backend.capabilities().then(setCaps); }, []);
  if (/^\/evaluations\/[^/]+\/report/.test(loc.pathname)) {
    return <Routes><Route path="/evaluations/:id/report" element={<ReportPage />} /></Routes>;
  }
  return (
    <div className="shell">
      <aside className="rail">
        <NavLink to="/" className="brand" end>
          <Mark />
          <span className="brand-name">ProjectIQ<span className="brand-sub">Project evaluation for faculty</span></span>
        </NavLink>
        <nav aria-label="Main">
          <NavLink to="/" end>Evaluations</NavLink>
          <NavLink to="/new">New evaluation</NavLink>
          <NavLink to="/rubrics">Rubric templates</NavLink>
        </nav>
        {caps && (
          <div className="rail-foot">
            <strong>{caps.kind === 'mock' ? 'Demo data' : 'Local backend'}</strong>
            {caps.name}
          </div>
        )}
      </aside>
      <main className="main" id="main">
        <Routes>
          <Route path="/" element={<EvaluationsList caps={caps} />} />
          <Route path="/new" element={<NewEvaluation caps={caps} />} />
          <Route path="/rubrics" element={<RubricLibrary />} />
          <Route path="/runs/:runId" element={<RunProgress />} />
          <Route path="/evaluations/:id" element={<ResultsPage caps={caps} />} />
          <Route path="/evaluations/:id/:tab" element={<ResultsPage caps={caps} />} />
          <Route path="*" element={<div className="page"><h1>Page not found</h1></div>} />
        </Routes>
      </main>
    </div>
  );
}
