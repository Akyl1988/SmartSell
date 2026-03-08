# SmartSell Master Operating Vision

**Date:** 2026-03-07  
**Purpose:** итоговый документ, объединяющий текущее состояние SmartSell, стратегическое видение, operating model, приоритеты, ограничения, путь роста от 1 до 100 клиентов и практические условия перехода между этапами.  
**Positioning:** это не просто аудит и не просто roadmap. Это документ о том, чем SmartSell должен стать, чего нельзя допустить, и как именно довести платформу до устойчивого SaaS-состояния.

---

# 1. Моё видение SmartSell

## Что такое SmartSell на самом деле

SmartSell не должен быть просто:
- набором фич
- очередной админкой
- набором интеграций без единого операционного смысла

**SmartSell должен стать операционной платформой торговли для продавца на маркетплейсе.**

Практически это означает:
- магазин подключается один раз
- получает управляемый торговый контур
- снижает ручной хаос
- видит состояние ключевых процессов
- может работать стабильнее, быстрее и с меньшим количеством ошибок
- не зависит от постоянного ручного тушения пожаров

Главная ценность SmartSell — не в количестве кнопок. Главная ценность SmartSell — в том, что он **берёт на себя операционную нагрузку торгового бизнеса** и делает её управляемой.

## Что SmartSell не должен собой представлять

SmartSell не должен стать:
- “всем для всех”
- системой с кучей фич и без operating layer
- founder-only платформой, которая работает только пока один человек всё помнит
- архитектурно красивым, но коммерчески бесполезным продуктом

## Моё дополнительное видение

SmartSell должен продавать не “автоматизацию вообще”, а **снижение операционной неопределённости магазина**.

Покупатель SmartSell должен понимать:
- что у него стало под контролем
- что будет видно сразу
- где будут предупреждения до сбоя
- как понять, что система работает правильно
- что делать, если что-то пошло не так

То есть SmartSell должен стать не просто “сервисом”, а **операционной оболочкой магазина**.

---

# 2. Executive Summary

## Bottom line

SmartSell уже **существенно сильнее типичного MVP**. Это уже не сырая идея, а реальная многотенантная платформа с работающим техническим ядром.

У SmartSell уже есть:
- multi-tenant foundation
- RBAC direction
- wallet / subscriptions foundation
- reports / exports direction
- Kaspi integration domain
- background workers / scheduler direction
- repricing / preorder / inventory direction
- runbooks
- smoke coverage
- operational/admin surfaces in progress

Но SmartSell пока ещё не является **полноценной зрелой SaaS operating system**.

Текущее состояние можно описать так:

**technically strong operator-assisted SaaS**

То есть:
- ядро уже сильное
- запуск первых клиентов реален
- но рост будет ломать систему, если не достроить operating layer вокруг уже существующего ядра

## Практический вывод

- **1–3 клиента:** можно брать уже сейчас при аккуратном ручном контроле
- **4–10 клиентов:** можно брать после закрытия launch-critical P0 gaps
- **11–30 клиентов:** уже требуют формализации SaaS control layer
- **31–100 клиентов:** требуют зрелой operating model, governance, quotas, lifecycle, DR, support tooling и снижения founder dependency

## Ключевая формулировка

**SmartSell уже имеет сильный SaaS engine, но вокруг него ещё нужно достроить полноценную SaaS operating system.**

## Мой практический акцент

Главная проблема SmartSell сейчас не в отсутствии идеи и не в отсутствии модулей. Главная проблема — **execution discipline**:
- закрывать P0 до расширения scope
- запускать клиентов по стадиям
- не путать архитектурный прогресс с рыночным прогрессом
- не подменять operating maturity количеством фич

---

# 3. Текущая оценка готовности

## Readiness score

Это не математическая истина, а управленческая оценка текущего положения.

- **Architecture readiness:** 80%
- **Core product readiness:** 75%
- **Tenant isolation readiness:** 75%
- **Operational readiness:** 55%
- **Support readiness:** 40%
- **Billing governance readiness:** 50%
- **SaaS governance readiness:** 35%
- **Scale readiness:** 20%

## Readiness by client band

- **Ready for 1–3 clients:** 85%
- **Ready for 4–10 clients:** 65%
- **Ready for 11–30 clients:** 40%
- **Ready for 31–100 clients:** 20%

## Что это значит

SmartSell уже **достаточно силён для запуска**, но ещё **недостаточно организован для спокойного масштаба**.

---

# 4. Что уже есть в SmartSell

## Engineering foundation

Ниже — то, что уже существует как реальная база платформы:
- multi-tenant domain centered around `company_id`
- RBAC direction with platform/store separation
- wallet/subscription direction
- report/export direction
- admin task endpoints
- background worker/scheduler foundation
- Kaspi integration implementation
- repricing domain work
- preorder/inventory direction
- runbooks and smoke scripts
- non-trivial tests and contract-oriented checks

## Operational signs of maturity

- deployment/runbook thinking already exists
- smoke-based release verification exists in parts
- admin/operator workflows are already being considered
- production-safety thinking is visible in multiple areas

## Что пока частично и рискованно

- runtime ownership split between API lifecycle and worker/scheduler
- frontend auth/session hardening
- Kaspi supportability and failure visibility
- onboarding repeatability
- support diagnostics completeness
- billing edge-case operating model
- DR maturity
- incident management discipline

## Что в основном отсутствует как системный слой

- customer-data migration strategy
- feature flags / entitlements
- formal API lifecycle governance
- tenant full export / deletion lifecycle
- quotas / noisy-neighbor controls
- SaaS business metrics dashboard
- retention matrix
- customer lifecycle model
- support-grade admin tooling
- bus-factor reduction package
- cost model by growth band

## Моё дополнение

SmartSell уже выглядит как **ядро будущего SaaS**, но пока ещё не как **самодостаточная SaaS-операционная система**.

Разница между ними в том, что ядро может работать, а система должна:
- переживать сбои
- объяснять своё состояние
- поддерживаться без автора
- масштабироваться без постоянной импровизации

---

# 5. SaaS Maturity Model

## Лестница зрелости

1. **Prototype**
2. **MVP**
3. **Operator-assisted SaaS**
4. **Managed SaaS**
5. **Scalable SaaS**

## Позиция SmartSell

### Сейчас

**Между 3 и ранней 4**

### Цель по этапам

- **для 10 клиентов:** стабильный уровень 4
- **для 30 клиентов:** сильный уровень 4 с control systems
- **для 100 клиентов:** поздний 4 с переходом к 5

## Что значит текущее положение

SmartSell уже:
- может давать реальную ценность
- может обслуживать реальных клиентов
- может быть коммерчески полезным

Но пока слишком сильно зависит от:
- памяти основателя
- ручного контроля
- частичной операционной инфраструктуры
- недоформализованных политик и жизненных циклов

## Мой вывод

Самая опасная ошибка сейчас — считать, что SmartSell уже почти “готовый масштабируемый SaaS”. Нет. Он уже **достоин запуска**, но ещё **не заслужил масштаб**.

---

# 6. Ядро продукта

Если убрать всё вторичное, ядро SmartSell должно состоять из четырёх блоков.

## 6.1 Торговое ядро

- товары
- цены
- остатки
- заказы
- предзаказы
- интеграции
- состояния операций

## 6.2 Автоматизация

- репрайсинг
- scheduled work
- правила
- background tasks
- предсказуемое выполнение процессов

## 6.3 Контроль

- tenant visibility
- diagnostics
- error states
- request tracing
- operational dashboards

## 6.4 SaaS governance

- RBAC
- entitlements
- billing states
- quotas
- lifecycle
- retention
- export/delete
- incident / DR / release discipline

## Формула SmartSell

**SmartSell = торговое ядро + автоматизация + контроль + SaaS governance**

Если одного из этих блоков нет, платформа становится:
- либо сырой
- либо слишком дорогой в поддержке
- либо неспособной к росту

## Моё дополнение

Пятый скрытый слой SmartSell — это **доверие**. Его нельзя запрограммировать напрямую, но оно возникает из:
- предсказуемости
- видимости
- понятной поддержки
- правильной обработки ошибок
- честных ограничений

---

# 7. Каноническая архитектурная схема

## Architecture map

**Пользователь / Оператор → Frontend / Admin UI → API Layer → Service Layer → Workers / Scheduler → Redis / Queue → PostgreSQL → Integrations: Kaspi / Payments / Messaging / Other Providers**

## Что это означает practically

- API должен обслуживать запросы, а не жить как скрытый scheduler
- Worker layer должен быть единственным владельцем фонового исполнения
- Redis и queue — это средство координации, а не место для операционной магии
- Postgres — источник бизнес-истины
- Integrations должны быть видимыми, объяснимыми и диагностируемыми

---

# 8. Ключевые operating principles

## Принцип 1 — рост только по этапам

Не “0 → 100 сразу”, а:
- 1–3
- 4–10
- 11–20
- 21–30
- 31–50
- 51–100

## Принцип 2 — каждый этап требует нового слоя дисциплины

- сначала **technical survivability**
- затем **operational repeatability**
- затем **tenant control**
- затем **support/process governance**
- затем **cost/governance/organizational resilience**

## Принцип 3 — переход на следующий этап только через явные gate conditions

Следующий этап не открывается потому что “вроде всё работает”. Он открывается только если:
- критерии этапа выполнены
- риски снижены
- recovery/support реальны
- новый диапазон не обрушит систему

## Принцип 4 — во время launch hardening нельзя распыляться

Пока P0 gaps открыты:
- не расширять aggressively feature scope
- не заниматься широким рефакторингом ради красоты
- не усложнять биллинг до формализации state machine
- не полировать self-serve раньше support visibility
- не лечить tenant-specific cases хаотичными исключениями в коде

## Принцип 5 — риск всегда должен быть явным

Каждая проблемная область должна быть отмечена как:
- closed
- open
- blocked
- temporarily risk-accepted

## Принцип 6 — никакого скрытого hero mode

Платформа не должна держаться на режиме:
- “я помню, как это чинить”
- “я сам быстро зайду и поправлю”
- “клиенту пока так сойдёт”

Это допустимо для 1–3 клиентов, но недопустимо как operating model.

---

# 9. Что я считаю главным обещанием клиенту

Первое честное обещание SmartSell не должно быть про “AI” и не должно быть про “всё в одном”.

Оно должно звучать так:

**“SmartSell помогает магазину работать стабильнее, быстрее и с меньшим количеством ручных ошибок.”**

Это обещание должно подтверждаться пятью вещами:
1. магазин можно нормально подключить
2. основные процессы реально выполняются
3. ошибки видны и понятны
4. доступ и функции предсказуемы
5. платформа не разваливается от первого роста

Если это не выполнено, всё остальное вторично.

## Моё дополнение

Не надо обещать “полную автоматизацию бизнеса”. Надо обещать **контролируемую и понятную автоматизацию ключевых торговых операций**.

Это сильнее, честнее и продаётся лучше.

---

# 10. Launch Gate для первых 10 клиентов

SmartSell можно считать **готовым к контролируемому запуску** только когда одновременно выполнены следующие условия:

- runtime ownership explicit
- background execution не двусмысленен
- onboarding standardized
- billing failure / grace / suspension behavior defined
- tenant-scoped support diagnostics exist
- хотя бы один DR restore drill completed
- хотя бы один lightweight incident process exists
- auth/session risk reduced or consciously accepted
- release checklist and post-deploy smoke are actually used
- Kaspi failure visibility good enough for support

## Текущий launch verdict

**Продуктовая идея не блокирует запуск.** Запуск блокируется только:
- операционной недостроенностью
- несколькими P0 execution gaps
- founder-heavy dependency

## Моё дополнение

Для первых 10 клиентов нужен не “идеальный SaaS”, а **управляемый SaaS**. То есть такой, где:
- понятны риски
- видны ошибки
- есть план восстановления
- ограничен scope обещаний
- есть дисциплина запуска

---

# 11. P0 / P1 / P2

## P0 — launch-critical

Это то, что с наибольшей вероятностью вызовет боль у первых клиентов или хаос при запуске.

- runtime ownership split
- auth/session hardening decision
- onboarding playbook
- tenant diagnostics summary
- billing failure / grace / suspension policy
- DR baseline and first restore drill
- incident process
- Kaspi support visibility
- release discipline and mandatory post-deploy smoke

## P1 — нужно до комфортного роста

- data migration standard for tenant business data
- feature flags / entitlements
- API lifecycle policy
- tenant export design
- tenant archive/delete policy
- quotas catalog
- first quota enforcement
- SaaS metrics dashboard
- retention matrix
- customer lifecycle model
- support workflow formalization

## P2 — нужно для устойчивого масштаба

- module decomposition for oversized domains
- advanced billing logic
- richer support admin tooling
- self-serve onboarding improvements
- cost model by client band
- bus-factor reduction package
- canonical architecture diagram

## Мой приоритетный смысл

P0 — это не просто “важно”. Это **граница между управляемым запуском и хаотичным запуском**.

P1 — это граница между:
- “мы ещё тянем вручную”
- “мы уже не ломаемся от каждого нового клиента”

P2 — это граница между:
- “проект ещё живёт на воле основателя”
- “платформа может жить как система”

---

# 12. Dependency Map

## Ключевые зависимости

- **Billing state machine** → нужен для customer lifecycle, suspension logic, support workflow
- **Support diagnostics** → нужны для self-serve onboarding, richer support tooling, incident speed
- **Quota catalog** → нужен для first quota enforcement и cost governance
- **Retention matrix** → нужна для export/delete/archive policy
- **API lifecycle policy** → нужна для safe integrations and future client-facing stability
- **Runtime ownership cleanup** → нужен для reliable support, DR confidence, incident diagnosis
- **Tenant diagnostics summary** → нужна для Kaspi supportability and lower founder interruption
- **Bus-factor reduction** → зависит от runbooks, architecture map, release process, DR docs

## Что это означает

Нельзя делать некоторые вещи в произвольном порядке. Если сначала делать self-serve polish, а потом diagnostics, это будет ошибка. Если сначала делать advanced billing, а потом state machine, это будет ошибка.

---

# 13. SaaS gaps: чего реально не хватает

## 13.1 Runtime ownership and background processing

### Проблема

Часть orchestration still risks living between API lifecycle and worker/scheduler behavior.

### Почему это опасно

- duplicate execution
- races
- confusing production behavior
- hard-to-diagnose incidents

### Что нужно

- API request-serving only
- background tasks only in worker/scheduler roles
- explicit ownership and tests

### Status

**Partial**

## 13.2 Frontend auth / session hardening

### Проблема

Backend auth logic может быть сильнее, чем browser-side token handling.

### Что нужно

- hardened browser/session strategy
- clear revoke/refresh behavior
- tested end-to-end posture

### Status

**Partial**

## 13.3 Billing edge cases

### Что нужно определить

- failed payment
- retry policy
- grace period
- suspension behavior
- reactivation behavior
- downgrade timing
- admin override policy

### Status

**Partial**

## 13.4 Support diagnostics

### Что нужно

- tenant-scoped diagnostics
- last sync timestamps
- error history
- request tracing
- integration health summary

### Status

**Partial**

## 13.5 Disaster recovery

### Что нужно

- restore drill
- RPO / RTO
- owner
- recovery checklist
- degraded-mode rules

### Status

**Partial**

## 13.6 Incident management

### Что нужно

- severity rubric
- owner assignment
- internal/customer comms templates
- postmortem template
- action tracking

### Status

**Mostly Missing**

## 13.7 Data lifecycle and tenant business data migration

### Что нужно

- tenant-safe backfill standard
- dry-run mode
- audit trail
- validation report
- rollback/compensation strategy

### Status

**Missing**

## 13.8 Feature flags and entitlements

### Что нужно

- entitlements model
- feature checks
- plan-linked access model
- override audit trail
- central helper

### Status

**Missing**

## 13.9 API lifecycle policy

### Что нужно

- version policy
- deprecation rules
- compatibility expectations
- contract discipline
- deprecation/announcement template

### Status

**Missing**

## 13.10 Data ownership / export / deletion

### Что нужно

- full tenant export design
- export manifest / integrity summary
- archive/delete policy
- export-before-delete rules
- audit export for platform ops

### Status

**Partial / Missing**

## 13.11 Tenant quotas and resource limits

### Что нужно

- product/report/sync/API/concurrency quotas
- soft warnings
- selective hard enforcement
- plan-linked rules
- visibility into tenant consumption

### Status

**Missing**

## 13.12 SaaS metrics

### Что нужно видеть

- orders synced by tenant
- integration failure rate
- repricing success/failure
- preorder failures
- billing conversion
- active tenants
- support incident rate
- MTTD / MTTR

### Status

**Missing as mature layer**

## 13.13 Customer lifecycle

### Нужные состояния

- trial
- active
- delinquent
- suspended
- recovered
- churned
- archived

### Status

**Partial**

## 13.14 Data retention

### Что нужно

- retention matrix
- archive rules
- cleanup ownership
- export-before-delete path

### Status

**Missing**

## 13.15 Bus-factor

### Что нужно

- handover-ready runbooks
- architecture map
- release/DR/support docs
- operator-independent recovery paths

### Status

**Missing**

## 13.16 Cost/scaling governance

### Что нужно

- infra model by client band
- support cost by client band
- operator time by client band
- heavy-tenant cost awareness
- threshold where margin breaks

### Status

**Missing**

---

# 14. Product Readiness Layer

## Onboarding UX

Нужно:
- один стандартный onboarding path
- один checklist per tenant
- visible activation status
- integration validation
- first-success milestone

## Billing UX

Нужно:
- plan clarity
- trial clarity
- renewal/failure/suspension messaging
- clear operator/admin override behavior

## Integration UX

Нужно:
- readable Kaspi connection status
- recent sync visibility
- understandable failure messages
- credential validation feedback

## Support workflow

Нужно:
- one support entry path
- one triage flow
- one accountable owner

## Product verdict

**Usable for first clients if managed closely, not yet smooth enough for scaled self-serve growth.**

## Моё дополнение

На этом этапе UX должен быть не “красивым”, а **операционно ясным**. То есть пользователь и оператор должны сразу понимать:
- что подключено
- что не подключено
- что сломано
- кто должен действовать
- что делать дальше

---

# 15. Operations Readiness Layer

## Backup policy

Нужно определить:
- frequency
- retention
- storage responsibility
- encryption/location
- restore owner

## Alerting

Нужны alerts на:
- worker failures
- repeated integration failures
- billing failures
- queue backlog
- DB/resource pressure

## Incident playbooks

Нужны короткие playbooks для:
- Kaspi sync failure
- billing renewals failing
- queue backlog
- auth/session outage
- migration issue

## Support channel

Нужен один defined support path and response process.

## Release discipline

Нужны:
- migration-before-app-switch discipline
- rollback path
- post-deploy smoke
- release owner
- release gate

## Operations verdict

**Engineering intent exists, but formal operational policy is not yet complete.**

## Моё дополнение

Без этого слоя SmartSell будет выглядеть работающим, но не будет **надёжным**. А для SaaS надёжность важнее количества функций.

---

# 16. Risk Acceptance Table

## Must fix before 10 clients

- runtime ownership ambiguity
- billing state ambiguity
- missing tenant diagnostics
- no restore drill proof
- no incident process
- no enforced release checklist

## Can launch with compensating controls

- partial onboarding UX polish
- partial support admin tooling
- incomplete self-serve setup
- incomplete cost model
- incomplete advanced billing logic

## Must fix before 30 clients

- no feature-gating model
- no quota model
- no API lifecycle policy
- no retention matrix
- no customer lifecycle governance
- no export/delete path

## Must fix before 100 clients

- no bus-factor reduction
- no regular DR practice
- no mature support tooling
- no modularization progress
- no cost governance

---

# 17. Execution Board

| Area | Priority | Status | Owner | ETA | Evidence / Current State | Exit Criteria | Blockers |
|---|---|---|---|---|---|---|---|
| Runtime ownership split | P0 | Partial | Founder/Backend | 3–5 days | Background execution direction exists, but ownership remains mixed | API request-only; background work only in worker/scheduler roles | Lifecycle coupling |
| Frontend auth/session hardening | P0 | Partial | Founder/Frontend | 2–4 days | Backend auth stronger than browser posture | Hardened session/token strategy; revoke/logout tested | Current frontend storage model |
| Standard onboarding playbook | P0 | Partial | Founder/Ops | 2–3 days | Onboarding possible but operator-driven | One checklist, one owner, one rollback path, one evidence pack | No standardized activation flow |
| Tenant diagnostics summary | P0 | Partial | Founder/Backend | 3–5 days | Diagnostics exist only in parts | Tenant support surface shows sync/error/request/integration health | Data scattered |
| Billing failure/grace/suspension policy | P0 | Partial | Founder/Product+Backend | 2–3 days | Core billing exists; edge policy incomplete | Subscription state machine written and supportable | Policy decisions pending |
| DR baseline and restore drill | P0 | Partial | Founder/Ops | 2–4 days | Backup direction exists but no proven drill | Restore drill completed; RPO/RTO documented | No completed drill evidence |
| Incident process | P0 | Missing | Founder/Ops | 1–2 days | No formal incident layer | Severity rubric, owner rule, templates exist | No process |
| Kaspi support visibility | P0 | Partial | Founder/Backend | 3–5 days | Kaspi works but remains support-heavy | Last success/failure visible; errors understandable | Integration complexity |
| Release checklist and smoke gate | P0 | Partial | Founder/Ops | 1–2 days | Smokes exist but not one enforced gate | Release checklist documented and used | No single enforced gate |
| Data migration standard | P1 | Missing | Founder/Backend | 4–6 days | Schema migration exists; tenant data standard does not | Dry-run, tenant-batch, validation, rollback conventions defined | Need policy/tooling |
| Feature flags / entitlements | P1 | Missing | Founder/Backend | 4–7 days | Ad hoc gating only | Central entitlements model/helper; overrides audited | Need schema + helper |
| API lifecycle policy | P1 | Missing | Founder/CTO | 2–3 days | Version path exists, governance does not | Compatibility/deprecation policy written and followed | Governance decision |
| Tenant full export design | P1 | Partial | Founder/Backend | 3–5 days | Module exports exist; full export does not | Export job/manifest/runbook exists | Scope/format decision |
| Tenant archive/delete policy | P1 | Missing | Founder/Product+Backend | 2–4 days | No full lifecycle policy | Archive/delete states defined; export-before-delete linked | Policy needed |
| Tenant quotas catalog | P1 | Missing | Founder/Product+Backend | 3–5 days | No centralized limit layer | Quota catalog defined; visibility exists | Plan linkage needed |
| First quota enforcement | P1 | Missing | Founder/Backend | 3–5 days | Heavy tenant risk remains open | At least one expensive operation has hard enforcement | Depends on quota model |
| SaaS metrics dashboard | P1 | Missing | Founder/Ops+Backend | 4–6 days | Technical logs exist; business-health view does not | Dashboard/report shows tenant health and SaaS metrics | Metrics definitions needed |
| Retention matrix | P1 | Missing | Founder/Ops | 2–3 days | No formal policy | Matrix by data type defined with responsibility | Product/legal/business decision |
| Support workflow / triage lane | P1 | Partial | Founder/Ops | 2–3 days | Support too engineer-context dependent | One intake path; one triage flow; severity mapping | No unified workflow |
| Customer lifecycle model | P1 | Partial | Founder/Product | 2–4 days | Trial/activation direction exists but states incomplete | Lifecycle states documented and used operationally | Depends on billing policy |
| Module decomposition | P2 | Partial | Founder/Backend | 2–4 weeks | Oversized domains exist | Refactor plan approved; first extractions completed | Competes with launch work |
| Advanced billing logic | P2 | Missing | Founder/Product+Backend | 2–3 weeks | Later-stage capability only | Proration/plan-change complexity implemented | Depends on product policy |
| Rich support admin tooling | P2 | Partial | Founder/Backend | 2–3 weeks | Minimal diagnostics needed first | Support panel shows events/history/replay where safe | Depends on diagnostics base |
| Self-serve onboarding improvements | P2 | Partial | Founder/Product+Frontend | 2–4 weeks | Still operator-assisted | Most activation steps self-serve with feedback | Depends on diagnostics + UX |
| Cost model by client band | P2 | Missing | Founder/Ops | 2–3 days | Scaling cost not formalized | Cost model exists for 10/30/100 | Hosting/usage assumptions needed |
| Bus-factor reduction package | P2 | Missing | Founder/Ops | 3–5 days | Knowledge concentration risk high | Critical runbooks and handover docs completed | Documentation time |
| Canonical architecture diagram | P2 | Missing | Founder/CTO | 1–2 days | Architecture described, not visualized | Concise diagram exists | Needs canonical version |

## Моё дополнение

Этот board должен стать **живым документом исполнения**, а не красивой таблицей. Если статус не обновляется, доказательства не прикладываются и критерии закрытия не проверяются — board бесполезен.

---

# 18. Growth Roadmap: 1 → 100 Clients

## Band 1 — 1 to 3 Clients

### Goal

Validate real usage with founder-led operation.

### Operating model

- manual
- direct support
- narrow client set
- limited surface area

### What must be true

- onboarding works
- auth works end-to-end
- one integration path works
- backup exists
- manual recovery path exists
- tenant issues can be inspected

### Hard stop conditions

- unresolved tenant isolation issue
- ambiguous background execution
- inability to recover from bad deploy
- inability to explain billing/access state

### Exit criteria

- first 3 tenants onboarded repeatably
- onboarding checklist used
- one restore drill completed
- incident template exists

## Band 2 — 4 to 10 Clients

### Goal

Controlled paid launch.

### What must be added

- launch gate
- billing edge policy
- tenant diagnostics
- release checklist
- incident rubric
- completion evidence pack per tenant

### Hard stop conditions

- no diagnostics
- no DR proof
- billing state ambiguity
- founder-only onboarding knowledge
- no release checklist in use

### Exit criteria

- 10 tenants supportable without panic
- support can diagnose common issues
- billing state explicit
- P0 launch board closed or risk-accepted

## Band 3 — 11 to 20 Clients

### Goal

Reduce fragility and stop relying on founder memory.

### What must be added

- support workflow
- lifecycle model
- retention matrix
- SaaS metrics dashboard
- quota draft
- API policy draft

### Hard stop conditions

- no support triage flow
- no metrics review
- no lifecycle clarity
- no owner for incidents/releases

### Exit criteria

- lifecycle states documented and used
- retention matrix exists
- metrics reviewed
- quota model defined
- API policy written

## Band 4 — 21 to 30 Clients

### Goal

Install the SaaS control layer.

### What must be added

- feature flags / entitlements
- quotas and first enforcement
- tenant export design
- archive/delete policy
- business-data migration standard
- stronger diagnostics

### Hard stop conditions

- no entitlement system
- no quota enforcement on expensive operations
- no export path
- unsafe business-data changes
- growing tenant-specific exceptions in code

### Exit criteria

- entitlements used centrally
- quota catalog live
- at least one quota enforced
- export path tested
- migration/backfill standard in use

## Band 5 — 31 to 50 Clients

### Goal

Become a managed SaaS operation.

### What must be added

- richer support tooling
- business dashboards
- improved onboarding UX
- cost model
- bus-factor reduction
- stronger runbooks

### Hard stop conditions

- support still requires founder for normal issues
- no cost model
- no handover-ready docs
- no weekly operational dashboard review

### Exit criteria

- support tools reduce engineering interruption
- cost model reviewed
- critical runbooks usable by another operator
- dashboards used in weekly review

## Band 6 — 51 to 100 Clients

### Goal

Operate as scalable SaaS, not founder survival mode.

### What must be added

- deeper modularization
- advanced billing logic
- more self-serve onboarding/support
- mature incident management loop
- regular DR drills
- plan-based quota governance
- living architecture documentation

### Hard stop conditions

- founder-exclusive recovery knowledge
- no regular DR practice
- no quota governance
- no lifecycle governance
- architecture debt causing unsafe changes

### Exit criteria

- incidents handled by process, not improvisation
- quotas/entitlements are trusted
- lifecycle governed
- export/retention/deletion rules operate in practice
- major domains modular enough for safe change

## Моё дополнение

Самый опасный переход — **10 → 30**. Именно там:
- заканчивается память основателя как operating system
- начинается реальная стоимость ручной поддержки
- появляются тяжёлые клиенты
- начинают вредить ad hoc исключения
- становится видно, есть ли у тебя SaaS control layer или нет

---

# 19. KPI Framework

## Launch KPIs (1–10 clients)

- onboarding time per tenant
- time to first successful integration sync
- number of onboarding steps requiring manual engineer intervention
- number of P0 incidents in first 30 days per tenant
- restore drill success / recovery time
- percentage of tenant issues diagnosable without raw code spelunking
- billing issue resolution time

## Stabilization KPIs (11–30 clients)

- MTTR by incident class
- failed sync rate by tenant/integration
- support tickets per tenant per month
- percentage of incidents requiring founder intervention
- percentage of tenants with clean lifecycle state alignment
- report latency for common reports
- quota warning count by tenant

## Scale KPIs (31–100 clients)

- cost per tenant band
- support hours per tenant
- top 10 heavy-tenant resource share
- billing delinquency resolution time
- DR drill frequency / success
- release rollback frequency
- percentage of critical ops runnable without founder
- change failure rate by major domain

## Моё дополнение

KPI нужны не для красоты. Они нужны, чтобы не спорить ощущениями.

Если нет метрик, платформа начинает жить на иллюзиях:
- “кажется, стало лучше”
- “кажется, всё нормально”
- “кажется, клиенты довольны”

Для SaaS это опасно.

---

# 20. Hard Stop Conditions Summary

## Before 10 clients

- no restore drill proof
- no tenant diagnostics
- no billing state clarity
- ambiguous runtime ownership
- no incident process

## Before 30 clients

- no feature gating model
- no quota model
- no API lifecycle policy
- no lifecycle states
- no retention matrix
- no export/deletion path

## Before 100 clients

- no cost model
- no bus-factor reduction
- no regular DR practice
- no mature support tooling
- no modularization progress in oversized domains
- no trusted governance around quotas/lifecycle/billing

## Моё дополнение

Эти hard stops нельзя “объяснить”. Их можно только:
- закрыть
- либо сознательно принять риск и ограничить рост

---

# 21. Non-Negotiables

Вот что я считаю принципиально недопустимым:

1. расширять scope при открытых P0  
2. строить self-serve до появления нормальной diagnostics/support visibility  
3. усложнять billing до формализации state machine  
4. делать широкий архитектурный рефакторинг вместо launch hardening  
5. лечить tenant differences хаотичными `if tenant == X` вместо policies / entitlements / quotas  
6. расти дальше без restore drill и incident discipline  
7. жить founder-only режимом после 10–20 клиентов  
8. обещать рынку больше, чем реально поддерживается операционно  
9. считать ручной саппорт “временной мелочью”, если он уже съедает фокус  
10. путать реальную продуктовую ценность с количеством реализованных фич

---

# 22. Owner Model

## Рекомендуемое распределение

- **Founder/Backend** — runtime, diagnostics, entitlements, quotas, export, billing mechanics
- **Founder/Frontend** — auth hardening, onboarding UX, support views
- **Founder/Ops** — DR, incident process, release checklist, support workflow, retention, cost model
- **Founder/Product** — billing policy, lifecycle states, quotas by plan, support expectations

## Практический смысл

Даже если большую часть делает один человек, owner model всё равно нужен. Он снижает хаос переключений и показывает, где скрыт bus-factor.

## Моё дополнение

Даже если сейчас все owner роли — это один человек, **мыслить ими всё равно обязательно**. Иначе работа идёт как каша:
- сегодня ты backend
- завтра саппорт
- потом бизнес
- потом релиз
- потом firefighting

Так проект быстро теряет управляемость.

---

# 23. Decision Log

Этот раздел нужен для того, чтобы SmartSell не зависал на вопросах, которые являются не задачами реализации, а задачами выбора.

## Правило

Если блокер упирается не в код, а в выбор политики, он обязан попасть в `Decision Log`.

## Формат записи

- **Decision**
- **Owner**
- **Deadline**
- **Options Considered**
- **Chosen Option**
- **Why Chosen**
- **Impacted Sections**
- **Status**

## Начальный список обязательных решений

### 23.1 Billing Grace Period
- **Decision:** сколько длится grace period после неуспешного списания
- **Owner:** Founder/Product
- **Deadline:** до закрытия P0 billing policy
- **Options Considered:** 0 / 3 / 7 / 14 дней
- **Chosen Option:** TBD
- **Why Chosen:** TBD
- **Impacted Sections:** Billing UX, customer lifecycle, suspension behavior, support workflow
- **Status:** Open

### 23.2 Suspension Behavior
- **Decision:** что именно происходит при suspended state
- **Owner:** Founder/Product + Backend
- **Deadline:** до запуска первых 10 клиентов
- **Options Considered:**
  - read-only mode
  - block writes, allow reads
  - full access block
  - partial feature freeze
- **Chosen Option:** TBD
- **Why Chosen:** TBD
- **Impacted Sections:** lifecycle, billing governance, support, UX
- **Status:** Open

### 23.3 Quota Thresholds
- **Decision:** какие лимиты считаются базовыми для первых планов
- **Owner:** Founder/Product
- **Deadline:** до quota catalog
- **Options Considered:** per products / per orders / per reports / per sync frequency / per API usage
- **Chosen Option:** TBD
- **Why Chosen:** TBD
- **Impacted Sections:** quotas, pricing, support, cost governance
- **Status:** Open

### 23.4 Retention Policy
- **Decision:** сколько хранить логи, sync payloads, отчёты, исторические данные
- **Owner:** Founder/Ops
- **Deadline:** до retention matrix finalization
- **Options Considered:** short / medium / long retention by data class
- **Chosen Option:** TBD
- **Why Chosen:** TBD
- **Impacted Sections:** data retention, export/delete, cost governance, compliance
- **Status:** Open

### 23.5 Export / Delete Policy
- **Decision:** как устроен export-before-delete и архивирование tenant data
- **Owner:** Founder/Product + Backend
- **Deadline:** до readiness for 30 clients
- **Options Considered:** immediate delete / soft archive / export + delayed purge
- **Chosen Option:** TBD
- **Why Chosen:** TBD
- **Impacted Sections:** data ownership, lifecycle, retention, trust
- **Status:** Open

### 23.6 Entitlements by Plan
- **Decision:** какие функции входят в какие тарифы
- **Owner:** Founder/Product
- **Deadline:** до feature entitlements implementation
- **Options Considered:** minimal / pro / advanced / custom
- **Chosen Option:** TBD
- **Why Chosen:** TBD
- **Impacted Sections:** pricing, support, feature flags, growth model
- **Status:** Open

## Моё дополнение

Если решения не фиксируются отдельно, проект начинает “делать код вокруг тумана”.  
Это один из самых частых источников скрытых задержек и хаотичных откатов.

---

# 24. Evidence Discipline

## Главное правило

**Ни один статус не может считаться изменённым без подтверждающего evidence.**

### Допустимые виды evidence
- PR / commit
- passing tests
- smoke result
- updated runbook
- migration result
- screenshot of working flow
- dashboard/report evidence
- log summary with clear outcome

## Правило смены статуса

### `Missing → Partial`
Только если:
- реализация начата
- есть код или runbook
- есть хотя бы одно доказательство, что направление реально существует

### `Partial → Exists`
Только если:
- поведение реализовано
- tests pass
- smoke or operational verification exists
- docs/runbook updated if needed
- support/recovery implications understood

### `Exists → Risk Accepted`
Только если:
- issue not fully resolved
- leadership consciously accepts temporary gap
- mitigating controls are documented
- expiry/review date is assigned

## Обязательные поля для evidence в execution board

Для каждой строки board желательно иметь:
- **Evidence Reference**
- **Date Verified**
- **Verified By**

## Моё дополнение

Без evidence execution board превращается в самоуспокоение.  
С evidence он становится инструментом управления реальностью.

---

# 25. Definition of Risk Accepted

## Что такое Risk Accepted

`Risk Accepted` — это не “мы забили”.  
Это значит:
- проблема известна
- риск понятен
- риск временно принимается сознательно
- есть компенсирующие меры
- есть срок пересмотра
- есть ограничение, до какого этапа этот риск допустим

## Обязательные поля для Risk Accepted

- **Risk**
- **Reason Accepted**
- **Compensating Controls**
- **Accepted By**
- **Acceptance Date**
- **Revisit Date**
- **Latest Allowed Stage**
- **Failure Trigger**

## Пример

### Partial onboarding UX polish
- **Risk:** onboarding still contains rough/manual steps
- **Reason Accepted:** does not block first controlled tenants
- **Compensating Controls:** operator-led onboarding checklist
- **Accepted By:** Founder/Product
- **Acceptance Date:** TBD
- **Revisit Date:** TBD
- **Latest Allowed Stage:** not beyond 10 clients
- **Failure Trigger:** onboarding time > target or repeated activation confusion

## Жёсткое правило

Если риск принят, но:
- нет compensating controls
- нет revisit date
- нет latest allowed stage

то это **не Risk Accepted**, а просто открытая проблема.

## Моё дополнение

Эта секция нужна, чтобы SmartSell рос честно.  
Не всё нужно чинить сразу, но всё должно быть либо закрыто, либо осознанно ограничено.

---

# 26. Weekly Review Cadence

## Цель weekly review

Не обсуждать всё подряд, а поддерживать управляемость SmartSell как SaaS-проекта.

## Раз в неделю обязательно проверять

### 26.1 P0 / P1 status changes
- что закрыто
- что зависло
- что появилось новым блокером

### 26.2 KPI movement
- что улучшилось
- что ухудшилось
- где появились тревожные отклонения

### 26.3 Evidence review
- какие статусы обновлены
- по каким из них есть реальные подтверждения
- где статус изменён без evidence

### 26.4 Risk review
- какие риски закрыты
- какие риски приняты временно
- какие risk accepted приближаются к пределу допустимости

### 26.5 Launch readiness
- что ещё блокирует 10 клиентов
- что уже достаточно стабильно
- где есть ложное чувство готовности

### 26.6 Focus discipline
- на что ушла неделя
- было ли распыление
- что не должно съесть следующую неделю

## Формат weekly review

- **Closed this week**
- **Still blocked**
- **New risks**
- **Evidence added**
- **Metrics movement**
- **Accepted risks**
- **Top priorities next 7 days**
- **What not to touch next week**

## Правило weekly review

Если weekly review пропускается несколько недель подряд, execution discipline начинает разрушаться даже при хорошем коде.

## Моё дополнение

Именно weekly cadence превращает сильный документ в рабочую систему управления.

---

# 27. Definition of Done

Задача считается закрытой не тогда, когда “код написан”, а когда выполнены все применимые условия:

- поведение реализовано
- роль и tenant boundaries соблюдены
- tests pass
- smoke or operational verification exists
- docs/runbook updated
- support can понять состояние без чтения исходников
- rollback/recovery path известен
- evidence attached in board

## Уровни done

### Engineering Done
Код, тесты, базовая реализация.

### Operational Done
Есть runbook, recovery logic, support understanding.

### SaaS Done
Поведение вписано в lifecycle, policies, governance и growth-stage constraints.

## Моё дополнение

SmartSell нельзя вести только по engineering done.  
Для SaaS нужен минимум operational done, а для growth-critical capability — SaaS done.

---

# 28. ETA Summary

## Phase 1 — Safe launch band

**~2–4 weeks** Covers P0 items.

## Phase 2 — Controlled growth band

**~4–8 additional weeks** Covers most P1 items.

## Phase 3 — Durable scale band

**~2–4+ months** Covers P2 items and deeper modularization/automation.

Это плановые оценки, не гарантии.

## Моё дополнение

Это оценки для **дисциплинированного фокуса**. Если начнётся распыление на:
- новые фичи
- случайные рефакторинги
- побочные идеи
- лишние ветки развития

сроки легко уедут в 2–3 раза.

---

# 29. Immediate Next 7 Actions

1. freeze non-essential scope for launch work  
2. close runtime ownership ambiguity  
3. write billing state machine and grace/suspension behavior  
4. build tenant diagnostics summary  
5. write onboarding checklist and tenant evidence template  
6. run and document first restore drill  
7. create incident severity rubric + response template  

Если даже только эти 7 шагов будут сделаны качественно, SmartSell станет заметно безопаснее для первых платящих клиентов.

## Моё дополнение

Это и есть **реальный ближайший фронт работы**. Не “глобально улучшить проект”, а закрыть именно эти 7 вещей.

---

# 30. What Not To Do Yet

- broad refactor for elegance only
- advanced plan complexity before billing policy is stable
- self-serve polish before support visibility exists
- aggressive feature expansion during launch hardening
- premature scale optimization without quotas/lifecycle controls

## Моё дополнение

Главная дисциплина ближайшего этапа — **не делать лишнего**.

---

# 31. Final Verdict

## Honest conclusion

SmartSell уже имеет серьёзную техническую базу. Ему не нужен новый концепт. Ему нужна дисциплинированная достройка operating layer вокруг уже созданного ядра.

## Мой практический вывод

- **готов к 1–3 клиентам:** да, при аккуратной ручной эксплуатации
- **готов к 4–10 клиентам:** да, после закрытия launch-critical P0
- **готов к 11–30 клиентам:** некомфортно без SaaS control layer
- **готов к 31–100 клиентам:** небезопасно без governance, quotas, support maturity, DR discipline, lifecycle controls и снижения founder dependency

## Финальная формулировка

**SmartSell должен превратиться из сильной инженерной платформы в управляемый, устойчивый и коммерчески жизнеспособный SaaS для реальных магазинов — и каждый следующий диапазон клиентов должен быть заслужен новой операционной зрелостью, а не оптимизмом.**

## Моё последнее дополнение

Сейчас SmartSell уже достоин не бесконечного анализа, а **режима исполнения**.

Следующий правильный режим проекта:
- один master vision
- один execution board
- один launch checklist
- один weekly review по статусам, рискам и evidence

Это и будет точкой, где SmartSell перестанет быть просто сильной разработкой и начнёт становиться **реально управляемым SaaS**.