import { useCallback, useEffect, useMemo, useState } from 'react';
import { backend } from '../api';
import type { EvaluationRecord, TeacherReview } from '../api/types';
import { buildViewModel } from './viewModel';

export function useEvaluation(id: string | undefined) {
  const [rec, setRec] = useState<EvaluationRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    if (!id) return;
    let live = true;
    if (version === 0) setRec(null);
    setError(null);
    backend.getEvaluation(id).then((r) => live && setRec(r)).catch((e) => live && setError(String(e.message || e)));
    return () => { live = false; };
  }, [id, version]);
  const reload = useCallback(() => setVersion((v) => v + 1), []);
  const setReview = useCallback((review: TeacherReview) => setRec((r) => (r ? { ...r, review } : r)), []);
  const vm = useMemo(() => (rec ? buildViewModel(rec) : null), [rec]);
  return { rec, vm, error, setReview, reload };
}
