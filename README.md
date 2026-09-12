# AlphaX PieceWork

Enterprise piece-rate wage management for **Frappe / ERPNext v15**. Shop-floor capture (desk, Job Card, offline tablet, IoT counter) → anti-fraud validation → QC defect routing → progressive incentives → statutory deduction caps → Journal Entry or HRMS Additional Salary posting.

Publisher: Neotec Integrated Solutions · License: Proprietary · Version: 1.0.0

## Requirements
- Frappe v15, ERPNext v15
- HRMS v15 **optional** (only for the "Additional Salary (HRMS)" posting mode and attendance enforcement)

## Install
**Frappe Cloud:** push this repository to GitHub, add the app to your bench group, deploy, then install on the site. Migrations run on deploy.

**Self-hosted bench:**
```bash
bench get-app https://github.com/<org>/alphax_piecework
bench --site <site> install-app alphax_piecework
```
The installer is idempotent (re-runs on every migrate): roles, custom fields, bilingual difficulty classes and defect codes, settings defaults. No fixtures.

## Configure (in this order)
1. **PieceWork Settings → Company Accounts**: wage expense, wage payable, default cost center (+ Salary Component for HRMS mode).
2. **Workstation** → `Max Pieces / Hour`; **Operation** → `Default SAM (minutes)`.
3. **Piece Rate Matrix**: one submitted row per operation (optionally narrowed by item / workstation type / difficulty). Most specific row wins. Amend with a reason to change rates.
4. **Production Cell** for team lines; **Employee Operation Skill** if skill enforcement is on.
5. Assign roles: PieceWork Manager, PieceWork Supervisor, PieceWork QC Inspector, PieceWork Device (edge integration users only).

## Daily flow
Supervisor submits **Piece Rate Logs** (or Job Cards / terminal / devices create them) → QC records **Shop Floor QA Logs** → manager reviews logs held over capacity (cannot approve own entries) → payroll creates a **PieceWork Wage Batch**, clicks *Fetch Entries*, submits → Journal Entry or Additional Salaries are posted (large batches post in the background; failures can be retried).

## Edge API
Token-authenticated user holding only the PieceWork Device role, bound to a PieceWork Edge Device.
```
POST /api/method/alphax_piecework.api.edge.ingest
Authorization: token <api_key>:<api_secret>
{"device_id": "TAB-01", "events": [{"client_uuid": "<uuid>", "employee": "HR-EMP-0001",
  "qty": 12, "from_time": "2026-09-10 08:00:00", "to_time": "2026-09-10 08:10:00",
  "work_order": null, "operation": null}]}
→ {"accepted": [...], "duplicates": [...], "rejected": [{"client_uuid": "...", "error": "..."}], "server_time": "..."}
```
Delete an event locally only when its uuid is acknowledged as accepted, duplicate or rejected. Replays are safe.
Also: `handshake(device_id)`, `work_orders(device_id)`.

## Screens & reports
- `/app/piecework-terminal` offline-first operator terminal
- `/app/shopfloor-leaderboard` overhead monitor board (EN/AR, no money shown, optional masked names)
- Reports: Piece Rate Earnings Register, Labor Effectiveness (OLE), Defect Pareto, Capacity Exceptions
- Print format: PieceWork Wage Statement (bilingual)

## Documentation
See `docs/AlphaX_PieceWork_v1.0.0_Implementation_Guide.docx` (and PDF) for the full implementation and administration guide.

## Tests
```bash
python3 verify_tree.py                                    # structural guard, run before every push
python3 -m unittest alphax_piecework.tests.test_calc      # pure engine, no site needed
# live scenario - TEST/STAGING SITES ONLY (writes and commits data; requires "allow_tests": true)
bench --site <test-site> execute alphax_piecework.tests.e2e_scenario.run
```

## Compliance note
Deduction caps (5 days' wage for damage, 50% total) and the minimum-earnings guarantee are configurable defaults modelled on commonly cited KSA Labor Law provisions. Confirm with legal counsel before go-live.
