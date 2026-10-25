# Skill candidate backlog — instructions to complete

Source: `TODO-skill-candidate-backlog.md` (generated from `CAPABILITIES.md` + `chronoa-matrix.json`).

Before implementing any candidate:
1. Read source code and tests; build coverage ledger.
2. Verify against authoritative skill registry / `capabilities._GROUPS`; do not hand-edit `CAPABILITIES.md`.
3. Confirm status (already covered / partial / missing / not applicable / deferred) with evidence.
4. Preserve consent gates, sandbox, local-first, timeout/cancellation, and redaction rules.
5. Implement in small batches with tests; regenerate `CAPABILITIES.md`; no duplicates.
6. Report exact results (files changed, tests run, limitations, deferred items).

Priority order per backlog: Phase 0 (coverage/registry); Phase 1 (performance/network/service reports); Phase 2 (reliability/backup/memory); Phase 3 (multimodal/workspace/model routing); Phase 4 (optional integrations).

Do not weaken existing permission/gate behavior. No unapproved cloud/telemetry. No stub-only registrations.
