# INCIDENT_DRILL_2026-03-14

- Date: 2026-03-14
- Candidate branch: dev
- Candidate commit: 52c498b
- Incident Owner: Ақыл
- Backup Incident Owner: Ақыл
- Rollback Owner: Ақыл
- Customer communication owner: Ақыл

## 1. Drill scenario

Scenario:
- During first-client launch window, Kaspi day-1 sanity check fails.
- The platform is reachable, but the required Kaspi integration path is not confirmed operational for the launch tenant.

Why this scenario matters:
- Kaspi is mandatory for first-client value.
- Failed Kaspi launch path means launch may need to pause or switch to NO-GO / rollback decision.

## 2. Incident classification

- Proposed severity: Sev-1
- Reason:
  - day-1 critical business capability unavailable
  - no acceptable launch value without Kaspi
  - customer impact is direct

## 3. Detection trigger

Incident is considered opened if any of the following happens during launch window:
- authenticated tenant-level Kaspi sanity fails
- required Kaspi sync/health path is unavailable
- launch tenant cannot complete mandatory Kaspi-related flow
- no safe workaround exists within launch window

## 4. Response timeline

### T+0 to T+5 min
- Confirm the failure is real and reproducible.
- Record exact failing command / endpoint / symptom.
- Freeze further launch steps.
- Mark incident as active.

### T+5 to T+15 min
- Determine scope:
  - only launch tenant?
  - broader Kaspi issue?
  - config/secrets issue vs product defect?
- Check health/readiness/basic platform status.
- Check whether this is recoverable inside launch window.

### T+15 to T+30 min
- Decide one of:
  - continue after validated fix
  - continue with explicit workaround
  - NO-GO for onboarding
  - rollback / launch abort

## 5. Communication plan

### Internal operator note
- Incident active
- Current severity: Sev-1
- Affected scope: first launch tenant, potentially broader Kaspi path until disproven
- Current hypothesis: Kaspi configuration, tenant auth context, external dependency issue, or product defect
- Next checkpoint in: 15 minutes

### Customer-facing update template
- We found an issue affecting the Kaspi launch path for your onboarding.
- We are actively validating scope and recovery options.
- We will provide the next update within 15 minutes.
- We will not proceed with activation until the required path is confirmed safe.

## 6. Rollback / NO-GO rule

Immediate NO-GO / rollback decision if:
- Kaspi day-1 path remains unavailable without safe workaround
- root cause is unknown inside launch window
- customer value at launch would be materially broken
- tenant safety / billing / platform integrity could be affected by forced continuation

## 7. Drill result

- Was the scenario understandable: Yes
- Was the decision path clear: Yes
- Was customer communication clear: Yes
- Was rollback authority clear: Yes
- Would this drill lead to safe operator behavior: Yes

## 8. Drill verdict

- Drill status: PASS
- Notes:
  - Incident handling path is operationally clear for a single-owner launch model.
  - Kaspi failure is correctly treated as a Sev-1 launch blocker.
  - Customer communication cadence and NO-GO trigger are defined clearly enough for launch control.
- Improvements needed before launch:
  - Attach one real Kaspi sanity evidence run during launch window.
  - Keep a copy-paste internal incident update template ready in the launch packet.
