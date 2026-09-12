# Changelog

## 1.0.0 — 2026-09-11
Initial release.
- 18 DocTypes: rate matrix with progressive slabs, piece rate log with cell splits, shop floor QA, wage batch, edge device/event, hash-chained audit event, settings.
- Anti-fraud: timestamp-derived hours, SAM + rated-speed capacity ceilings with skill factor, hold-for-review with segregation of duties, overlap detection, per-operation material balance with Work Order row lock, flow balance (BOM routing fallback).
- QC routing by responsibility; worker never charged for machine/material/design faults.
- Payroll: slabs on accepted pieces, statutory deduction caps, minimum guarantee, JE accrual or HRMS Additional Salary (with salary-structure pre-flight), stale-batch guard, background posting with retry.
- Edge: idempotent offline-first ingest, aggregation job, auto-submit, retention purge.
- Job Card bridge, 4 script reports, operator terminal, leaderboard, workspace, bilingual print format, Arabic translations.
- Verified on Frappe v15 + ERPNext v15 (with and without HRMS v15): clean install, uninstall, reinstall, 83/83 live scenario checks.
