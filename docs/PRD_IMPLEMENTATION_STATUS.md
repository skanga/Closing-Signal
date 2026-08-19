# U.S. Equities PRD implementation status

Last audited: 2026-08-19

This maps the approved baseline in `PRD_US_EQUITIES.md` to the repository.
`Implemented` means code plus offline automated evidence exists. `Partial` means
the core path exists but a documented release behavior or deployment artifact is
still required. `External gate` means engineering cannot create the contract,
identity, credential, or professional opinion.

## Status by requirement group

| Area | Status | Implemented evidence | Remaining work or gate |
|---|---|---|---|
| Configuration | Implemented | Strict JSON/TOML + environment precedence, secret-file rejection, aggregate validation, versioned strategy/backtest inputs, approved example profile | Replace the real domain/identity values and supply credentials |
| Universe/reference | Implemented | Alpaca catalog; OpenFIGI primary classifier; Nasdaq and SEC reconciliation; ambiguity/conflict quarantine; venue/type filtering; dated snapshots and symbol history | Credentialed acceptance test and periodic reference-refresh operating evidence |
| Market data/calendar | Implemented | Official Alpaca pagination, raw/split/all series, exact five/63-session correction windows, official session/early-close/DST handling, idempotent resume and quarantine | 252-session weekly reconciliation command remains P1; production SIP rights are an external gate |
| Six technical strategies | Implemented | All six U.S. strategies validate and load approved `us-research-v1` parameters | Research baselines must pass model review before being described as validated strategies |
| SEC discovery/classification | Implemented | Official recent/historical submissions, broad approved form inventory, ten-rps limiter, evidence rules, uncertain fallback, accession dedupe | Tier-aware immediate-vs-digest grouping and the approved polling timers are partial |
| Email delivery | Partial | Brevo-compatible SMTP/STARTTLS, HTML/plain text escaping, recipient idempotency, four-attempt retry policy, immutable attempts, 300-attempt UTC-day guard | One combined technical digest, non-direct SEC daily digest, hosted one-click unsubscribe, and credentialed Brevo acceptance |
| Subscriber consent | Implemented for private beta | Double-opt-in/policy/timestamp validation, deactivation evidence, immutable state-change audit, active-recipient enforcement | Public hosted consent/unsubscribe UI and provider suppression synchronization |
| Backtesting | Implemented | Production strategy reuse; next-open/close; costs/slippage/dividends/splits; rolling/anchored walk-forward; leakage-safe selection; manifests and reports | Automate VTI and 25/50-bps sensitivity matrix; delisted-history completeness remains provider-dependent |
| Commands/health | Implemented | Ten CLI commands, nonzero failures, mutation lock, reprocess/no-resend, active dependency/schedule/disk health | Ubuntu systemd unit/timer files and notification escalation procedure |
| Backup/retention | Partial | Portable canonical SQLite store and documented policy | Automated consistent encrypted local/offsite backup, pruning, and quarterly restore verification |
| Commercial/legal | External gate | Derived results only; no trading or raw-data redistribution; provider/feed recorded | SIP agreement, counsel sign-off, sender identity/domain, billing/tax/refund terms, final price validation |
| WhatsApp | P1 | Provider-neutral notification boundary | Official Cloud API integration, business account, templates, separate opt-in and suppression |

## Approved development profile

- Alpaca paper API with IEX only; no commercial production claim.
- OpenFIGI classification reconciled with Nasdaq and SEC data.
- Finalization delay 150 minutes; five-session bars and 63-session actions correction.
- Brevo Free, up to 25 initial subscribers and 300 provider attempts/day.
- SQLite on a local machine; Windows development and Ubuntu 24.04.4 LTS production target.
- Six experimental strategy baselines and the explicit backtest assumptions in
  the PRD/configuration examples.

## External commercial-production gates

1. Written SIP/commercial market-data rights and production entitlement.
2. Real organization, SEC contact, sender/support identities, and public domain.
3. SPF/DKIM/DMARC, Brevo/OpenFIGI/Alpaca/SEC credentialed acceptance evidence.
4. Hosted one-click unsubscribe and synchronized suppression.
5. Securities, privacy, commercial-email, terms, and financial-promotion counsel sign-off.
6. Named encrypted offsite backup destination and successful restoration drill.
7. Billing/tax/refund implementation and validated commercial pricing.

## Verification evidence

The release gate is:

```powershell
uv lock --check
uv run black --check .
uv run ruff check .
uv run mypy
uv run pytest --cov=closing_signal --cov-report=term-missing --cov-fail-under=80
uv run pip-audit
uv build
```

Current offline evidence: 96 tests pass; branch-aware coverage is 81.90%; Black,
Ruff, and strict mypy pass. Every shipped example configuration validates.
External credentialed tests are deliberately not represented by offline fixtures.
