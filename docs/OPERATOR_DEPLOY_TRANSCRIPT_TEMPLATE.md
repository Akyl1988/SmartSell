# OPERATOR_DEPLOY_TRANSCRIPT_TEMPLATE

Назначение: единый шаблон записи production-like deploy/restart rehearsal.

## 1. Metadata
- date: `YYYY-MM-DD HH:mm:ss ±TZ`
- branch: `<branch-name>`
- commit: `<commit-sha>`
- operator: `<name-or-id>`

## 2. Runtime startup commands
- API start command:
  - `<command>`
- scheduler start command:
  - `<command>`
- runner start command:
  - `<command>`

## 3. Health verification
- `/api/v1/health`:
  - status: `<200/other>`
  - timestamp: `<YYYY-MM-DD HH:mm:ss ±TZ>`
  - note: `<optional>`
- `/ready`:
  - status: `<200/other>`
  - timestamp: `<YYYY-MM-DD HH:mm:ss ±TZ>`
  - note: `<optional>`

## 4. Smoke verification
- auth:
  - command/test: `<command-or-test-id>`
  - result: `<pass/fail>`
- diagnostics:
  - command/test: `<command-or-test-id>`
  - result: `<pass/fail>`
- critical flow:
  - command/test: `<command-or-test-id>`
  - result: `<pass/fail>`

## 5. Observation window
- duration: `10–15 минут`
- window start: `<YYYY-MM-DD HH:mm:ss ±TZ>`
- window finish: `<YYYY-MM-DD HH:mm:ss ±TZ>`
- errors/no errors: `<errors/no errors>`
- notes:
  - `<incident-or-empty>`

## 6. Rollback decision
- rollback required? `<yes/no>`
- reason:
  - `<why>`

## 7. Operator sign-off
- operator:
- date:
- signature/comment:
