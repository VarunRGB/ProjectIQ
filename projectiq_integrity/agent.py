"""The Integrity Agent: runs the three checks for a cohort and returns one AgentResult
per submission. It gathers evidence only. It never assigns or changes a mark.

    agent = IntegrityAgent(code_dir=..., reports_dir=..., references_dir=..., base_code=...)
    agent.prepare()                    # JPlag once for the cohort, reports loaded once
    result = agent.analyze("S101")     # AgentResult with Findings
"""
import logging
from pathlib import Path
from typing import Dict, List, Optional

from .ai_indicators import SentenceScorer, ai_signals
from .config import DEFAULT, IntegritySettings
from .contracts import AgentResult
from .evidence import Signal, build_findings
from .jplag_runner import JPlagError, JPlagReport, code_signals, parse_result, prepare_submissions, run_jplag
from .report_similarity import _norm, load_folder, load_report, report_signals, template_sentences
from .ria.config import DEFAULT_SETTINGS as RIA_DEFAULTS, Settings as RiaSettings
from .ria.models import ModelUnavailableError

logger = logging.getLogger(__name__)
AGENT_NAME = "integrity"


class IntegrityAgent:
    def __init__(self, work_dir: Path, code_dir: Optional[Path] = None, reports_dir: Optional[Path] = None,
                 references_dir: Optional[Path] = None, base_code: Optional[Path] = None,
                 old_code_dirs: Optional[List[Path]] = None, template_report: Optional[Path] = None,
                 settings: IntegritySettings = DEFAULT, ria_settings: RiaSettings = RIA_DEFAULTS,
                 scorer: Optional[SentenceScorer] = None):
        self.work_dir = Path(work_dir)
        self.code_dir, self.reports_dir, self.references_dir = code_dir, reports_dir, references_dir
        self.base_code, self.old_code_dirs, self.template_report = base_code, old_code_dirs, template_report
        self.settings, self.ria_settings, self.scorer = settings, ria_settings, scorer
        self.jplag: Optional[JPlagReport] = None
        self.reports: Dict = {}
        self.references: Dict = {}
        self.shared: set = set()
        self.notes: List[str] = []

    def prepare(self) -> "IntegrityAgent":
        self.work_dir.mkdir(parents=True, exist_ok=True)
        if self.code_dir:
            try:
                counts = prepare_submissions(Path(self.code_dir), self.work_dir / "code")
                result = run_jplag(self.work_dir / "code", self.work_dir / "jplag" / "results",
                                   base_code=self.base_code, old_roots=self.old_code_dirs,
                                   settings=self.settings)
                self.jplag = parse_result(result)
                self.notes.append(f"Code: JPlag {self.jplag.version} compared {len(counts)} submissions"
                                  + (" with starter code excluded." if self.base_code else
                                     ". No starter code given, so shared starter code may count as similarity."))
                if self.jplag.failed:
                    self.notes.append(f"Code: JPlag could not parse {', '.join(self.jplag.failed)}.")
            except JPlagError as exc:
                self.notes.append(f"Code check skipped: {exc}")
        else:
            self.notes.append("Code check skipped: no code folder given.")
        self.reports = load_folder(self.reports_dir)
        self.references = load_folder(self.references_dir)
        template = load_report(Path(self.template_report)) if self.template_report else None
        self.shared = template_sentences(self.reports, template, self.settings) if self.reports else set()
        self.notes.append(f"Reports: {len(self.reports)} cohort reports, {len(self.references)} reference "
                          f"documents, {len(self.shared)} shared template sentences ignored.")
        return self

    def submission_ids(self) -> List[str]:
        ids = set(self.reports)
        if self.jplag:
            ids |= set(self.jplag.submissions)
        return sorted(ids)

    def analyze(self, sid: str) -> AgentResult:
        signals: List[Signal] = []
        notes = list(self.notes)
        if self.jplag:
            if sid in self.jplag.submissions:
                signals += code_signals(self.jplag, sid, self.settings)
            else:
                notes.append(f"No code found for {sid}.")
        if sid in self.reports:
            try:
                signals += report_signals(sid, self.reports, self.references, settings=self.settings,
                                          ria_settings=self.ria_settings, shared=self.shared)
            except ModelUnavailableError:
                notes.append("Report similarity skipped: the MiniLM model is not in models/. "
                             "Run 'python scripts/setup_offline.py' once (or set RIA_ALLOW_FALLBACK=1 "
                             "for a lexical-only run).")
            own_text = " ".join(s.text for s in self.reports[sid].body_sentences()
                                if _norm(s.text) not in self.shared)
            ai, ai_notes = ai_signals(own_text, f"{sid} report", scorer=self.scorer, settings=self.settings)
            signals += ai
            notes += ai_notes
        else:
            notes.append(f"No report found for {sid}.")
        findings = build_findings(signals)
        dropped = len(signals) - len(findings)
        if dropped:
            notes.append(f"{dropped} signal(s) dropped because they had no quoted evidence.")
        notes.append("All findings are advisory. The Integrity Agent never assigns or deducts marks.")
        return AgentResult(agent=AGENT_NAME, submission_id=sid, findings=findings, notes=notes)
