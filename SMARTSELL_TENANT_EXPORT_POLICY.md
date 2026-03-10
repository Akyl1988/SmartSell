# SmartSell Tenant Export Policy (MVP)

Version: 2026-03-09
Status: MVP preview-only

## Purpose

Define an export-before-delete capability for tenant data portability and operational control.
Current scope is manifest/preview only.

## Access

- Only platform admin can request tenant export manifest preview.

## MVP Included Domains

- company
- users
- products
- orders
- preorders
- campaigns
- subscriptions/billing summary
- repricing rules
- warehouses/inventory summary

## Not Included Yet

- binary media bulk dump
- external provider secrets raw values
- full infra backups
- hard delete

## Manifest Fields

- company_id
- company_name
- exported_at
- exported_by
- export_scope_version
- included_sections
- section_counts
- warnings
- not_included

## Rule

No data deletion on this step. This policy introduces preview metadata only.
