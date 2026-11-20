# ProjectIQ Executioner — implementation tracker

**Location:** Phase 2 of 8 → D5 security and reliability acceptance → B3b.

This document tracks practical acceptance, not promises of arbitrary untrusted
execution. Official scope remains in `ROADMAP.md`.

| Roadmap | Milestone | Evidence/status |
|---|---|---|
| 1 | GitHub ingestion, pinned manifest | Complete |
| 2A/2B | Planning and preflight | Complete (2B's Docker probe is legacy) |
| 2C | Disposable Ubuntu VM + static and PHP/MySQL | Real reviewed-fixture VM pilots verified |
| 2D D0–D3 | Integration, Vite, Python, Express | Reviewed fixture VMs verified |
| 2D D4a | Vite + Express + SQLite | Cross-service HTTP/SQLite fixture verified |
| 2D D5a | Clean-VM observations | Verified; not attested |
| 2D D5 B1 | Separate app UID, no root sudo | VM plus PHP/MySQL regression verified |
| 2D D5 B2 | Bounded workspace write quota | ENOSPC:RECOVERED VM verified |
| 2D D5 B3a | Inherited hard RLIMIT caps | VM verified (5/5); cgroup controllers available |
| 2D D5 **B3b** | **Systemd/cgroup enforcement** | Gate 1 7/7 clean VM; Gate 2A 434-test offline suite and FSDL/PHP/MySQL/Node reviewed fixtures verified; Gate 2A.2 independent live-server PID evidence staged for VM acceptance; builds and MySQL remain unverified |
| 2D D5 B4 | All-adapter regression/commit | Pending |
| 2D D5c | Failure injection/cancellation/orphan recovery | Pending |
| 2D D5d | Identity and independent isolation verification | Pending |
| 2D D5e | Final security go/no-go review | Pending |
| 3 | Project-specific grounded test generation | Not started |
| 4–8 | Functional testing, quality, diagnosis, reliability, agent integration | Not started |

**Current checkpoint policy:** keep D5a and all B milestones local until B4
passes. **Do not automatically run unreviewed repositories.** Final security
attestation must remain `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS` until independent
acceptance evidence supports a different determination.
