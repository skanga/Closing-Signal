# Product Requirements Document: Closing Signal

| Field | Value |
|---|---|
| Product | Closing Signal |
| Document version | 1.1 |
| Status | Product decisions approved; development/private-beta implementation in progress |
| Product owner | Confirmation recorded in the product discussion on 2026-08-18 |
| Initial market | United States |
| Initial deployment | Centrally operated on a local machine |
| Initial market-data provider | Alpaca |
| Initial data frequency | End of day |
| Initial product function | Screening, backtesting, and notifications |
| Initial notification channel | Email |

## 1. Executive summary

Closing Signal will be a centrally operated, commercial stock-screening
service for securities listed on the New York Stock Exchange and Nasdaq. It will
screen all provider-classified common stocks, exchange-traded funds, and American
depositary receipts in that universe. It will not apply minimum price, liquidity,
market-capitalization, or listing-age eligibility filters.

The first release will use Alpaca end-of-day market data from 2016 onward. It will
generate configurable strategy results after completed U.S. trading sessions,
email those results to centrally managed subscriber lists, monitor every
potentially relevant SEC offering filing, and provide a flexible historical
backtesting system. It will not place orders, manage portfolios, or provide a
brokerage interface.

The existing application is coupled to China's A-share market through baostock,
AKShare, six-digit symbols, fixed daily price-limit strategies, and Xueqiu links.
The U.S. product will replace those dependencies rather than maintain dual-market
behavior. Shared interfaces will nevertheless be used around providers,
notifications, storage, strategies, and backtesting so later provider, channel,
deployment, or intraday changes do not require another full rewrite.

Section 2.1 is the authoritative decision record. Names, domains, credentials,
legal advice, and provider contracts remain
external launch inputs and are not engineering assumptions.

## 2. Confirmed product decisions

The following decisions were supplied by the product owner and are requirements:

1. The product will support U.S. equities only; A-share support will be removed.
2. The product is intended to become a centrally operated commercial/shared service.
3. Alpaca is the initial market-data provider.
4. The first release is end-of-day; intraday and real-time support are future options.
5. The first release is a screener and notifier only; trading is a future option.
6. The eligible instrument types are common stocks, ETFs, and ADRs.
7. The eligible listing venues are NYSE and Nasdaq.
8. No price, liquidity, market-cap, listing-age, or similar eligibility filter will be applied.
9. China-specific price-limit strategies will be redesigned for U.S. market behavior.
10. SEC offering and private-placement monitoring is part of the first release.
11. Every potentially relevant filing will be reported; confidence thresholds will not suppress alerts.
12. Email is the initial notification channel; the architecture must permit alternatives.
13. The service will initially run on a local machine.
14. Alpaca history from 2016 onward is sufficient for the first release.
15. Backtesting must support transaction costs, slippage, configurable benchmarks,
    survivorship-bias controls, and walk-forward/out-of-sample evaluation.
16. IEX is limited to development and automated contract testing. A commercial
    production launch requires written rights for an appropriate SIP feed.

### 2.1 Approved implementation baseline

| Area | Approved decision |
|---|---|
| Reference data | OpenFIGI is the primary MVP common-stock/ETF/ADR classifier. Nasdaq Trader symbol directories and SEC company-ticker/exchange data reconcile it. Conflicts are quarantined; the system never infers type from a company name. |
| Market data | Alpaca paper endpoints and IEX are development/test only. The production profile must select SIP only after written commercial rights are confirmed. Yahoo Finance is not an authoritative or production dependency; it may be considered later only as a non-critical comparison source if its terms permit. |
| EOD timing | Finalization delay is 150 minutes after the official close. Normal targets are sync at 18:30 ET, screen at 18:45 ET, and digest at 19:00 ET. Re-fetch five completed sessions of bars and 63 sessions of corporate actions nightly; reconcile 252 sessions weekly and run a full quarterly audit. |
| Strategies | Version `us-research-v1` contains six experimental research baselines. They are screening hypotheses, not claims of expected return, and remain subject to backtest/model review. Exact values are in Section 7.5 and `config/strategies.example.json`. |
| SEC scope | Use the tiered form inventory in Section 7.6. Report every potentially relevant candidate, retain evidence, and label unmatched or conflicting classification as uncertain. Poll every ten minutes from 06:00–22:00 ET on weekdays and hourly otherwise. |
| Email | Brevo Free via authenticated STARTTLS SMTP on port 587 is the private-beta provider. Cap provider attempts at 300 per UTC day; begin with at most 25 subscribers. Retry four total times at 60, 300, and 1,800 seconds. Require double opt-in. |
| Routing | Technical selections form the daily digest branded **The Closing Signal**. Direct-offering SEC evidence is immediate; all other and uncertain SEC candidates form a daily digest during the free beta. Delivery grouping is a release requirement, not permission to suppress any filing. |
| Unsubscribe | During a monitored private beta, a published reply/support address may process requests within one business day and maintain suppression evidence. Public launch requires hosted one-click unsubscribe and synchronized suppression. |
| Alternative channels | WhatsApp is P1, using only the official WhatsApp Cloud API, separately recorded opt-in, approved templates where required, and its own suppression state. It is not a free MVP substitute for email. |
| Backtesting | SPY is primary and VTI is a sensitivity benchmark. Use $100,000, next-session open, 10% of initial capital per position, daily rebalance, $0 fixed + $0.005/share with $1 minimum, 10 bps slippage with 25/50 bps stress, and a disclosed constant 3% risk-free rate. Use rolling 756/252/252/252-session windows and maximize Calmar. |
| Holding periods | MA, high-tight-flag, Turtle, and RPS: 20 sessions. Gap-up shakeout and shock reversal: five sessions. Official runs fail on missing exits; a separate `mark_zero` stress test is required. |
| Local production host | Dedicated Ubuntu 24.04.4 LTS mini-PC, encrypted disk, UPS, wired network, dedicated service account, and systemd units/timers. Windows remains supported for development. |
| Backups | Nightly consistent encrypted SQLite backup, one local and one offsite copy, retaining 7 daily, 5 weekly, and 12 monthly copies. RPO 24 hours, RTO four hours, quarterly restore test. |
| Service level | Monthly target: 99% of completed-session digests by 20:00 ET and 95% of monitored direct SEC alerts within 20 minutes, excluding declared provider/market emergencies and maintenance. Subscriber support target is one business day. |
| Retention | Market data follows contract; reproducibility manifests seven years; delivery audit 13 months; operational logs 90 days; subscriber PII while active plus 30 days; consent/unsubscribe evidence seven years; suppression evidence indefinitely where lawful; raw provider response bodies no longer than 45 days. |
| Commercial model | Paid, ad-free newsletter: private beta 25–100 users, target $29/month founding and $49/month standard, 14-day trial, no ads, affiliate links, or personalized investment recommendations. Pricing remains subject to validation before billing implementation. |
| External release gates | Written SIP/commercial data rights, a real sender domain with SPF/DKIM/DMARC, credentialed provider acceptance tests, hosted one-click unsubscribe before public launch, restore-test evidence, and U.S. securities/privacy/commercial-email counsel sign-off. |

## 3. Product vision

An operator should be able to synchronize the complete eligible U.S. universe,
run a reproducible collection of end-of-day strategies, review results, and send
useful email alerts to subscribers without manually maintaining ticker lists or
spreadsheets. The same stored data and strategy definitions should support
historical evaluation with explicit assumptions and without silently using future
information.

The product should be provider-aware but not provider-bound. Alpaca is the first
implementation, not a permanent constraint on the domain model.

## 4. Goals

### 4.1 MVP goals

- Synchronize completed daily bars for every eligible NYSE and Nasdaq instrument.
- Maintain an authoritative local instrument catalog and daily universe snapshots.
- Run the six strategy families defined in this PRD.
- Replace China-specific limit-price logic with U.S.-appropriate volatility logic.
- Monitor and email every potentially relevant SEC offering filing.
- Send deduplicated HTML and plain-text strategy emails to configured subscribers.
- Backtest any implemented market strategy from available 2016-forward history.
- Make data, strategy, execution, cost, and benchmark assumptions explicit in every result.
- Recover safely from partial downloads, restarts, duplicate source records, and transient API failures.
- Preserve strict formatting, linting, typing, testing, and packaging gates.

### 4.2 Longer-term goals enabled by the architecture

- Intraday and real-time market data.
- Additional licensed market-data providers.
- Slack, Discord, Telegram, Feishu, and other notification channels.
- Cloud or container deployment.
- Web administration and subscriber self-service.
- Paper or live execution through a separate, explicitly authorized product phase.

These are architectural extension points, not MVP deliverables.

## 5. Non-goals

The MVP will not:

- Support Chinese A-shares or any non-U.S. listing venue.
- Include OTC securities, preferred shares, warrants, rights, units, mutual funds,
  options, futures, fixed income, foreign ordinary shares not represented by an
  eligible ADR, or cryptocurrencies.
- Place, route, simulate in real time, or manage orders.
- Connect to brokerage accounts for portfolio or position management.
- Promise investment performance or characterize results as personalized advice.
- Redistribute raw Alpaca bars, quotes, trades, or full source datasets to subscribers.
- Suppress SEC alerts based on a confidence score.
- Require a web UI, mobile application, billing system, or customer portal.
- Guarantee survivorship-bias-free results where the provider does not supply the
  required point-in-time or delisted-security data; limitations must instead be disclosed.

## 6. Users and operating roles

### 6.1 Service operator

The service operator installs and runs the product, owns the Alpaca and SEC API
configuration, schedules synchronization, manages subscribers, reviews failures,
and initiates reprocessing or backtests.

### 6.2 Subscriber

A subscriber receives strategy and SEC filing notifications by email. Subscriber
interaction beyond receiving email is not defined for the MVP.

### 6.3 Product administrator

The product administrator defines enabled strategies, strategy parameters,
benchmarks, cost assumptions, retention policy, email templates, and recipient
routing. This role may be performed by the service operator in the MVP.

### 6.4 Deferred commercial roles

The service operator owns support in the private beta. Billing administrators,
compliance reviewers, and subscriber self-service roles are deferred until the
corresponding hosted capabilities are approved.

## 7. Scope and functional requirements

Requirements use the following priorities:

- **P0:** required for MVP release.
- **P1:** required shortly after MVP or for operational maturity.
- **P2:** explicitly deferred extension.

### 7.1 Configuration and secrets

| ID | Priority | Requirement |
|---|---|---|
| CFG-001 | P0 | Load non-secret configuration from a documented file and environment variables. |
| CFG-002 | P0 | Load Alpaca credentials, email credentials, and SEC contact identity from environment variables or an OS-supported secret source. |
| CFG-003 | P0 | Never store secrets in the repository, database, logs, generated reports, or email bodies. |
| CFG-004 | P0 | Validate configuration before starting synchronization or notifications and report all invalid fields together where practical. |
| CFG-005 | P0 | Version strategy and backtest configuration so every result can identify the exact parameters used. |
| CFG-006 | P1 | Support separate development, test, and production configuration profiles without copying secrets. |

Use Brevo SMTP, TOML/JSON non-secret configuration, and environment-provided
secrets. Production secrets are restricted to the dedicated service account;
the exact OS secret facility may change without changing this contract.

### 7.2 Instrument universe

| ID | Priority | Requirement |
|---|---|---|
| UNI-001 | P0 | Obtain the instrument catalog from Alpaca rather than a manually curated ticker file. |
| UNI-002 | P0 | Include instruments listed on NYSE or Nasdaq that Alpaca classifies as common stock, ETF, or ADR. |
| UNI-003 | P0 | Exclude all other venues and instrument types. |
| UNI-004 | P0 | Apply no price, volume, dollar-volume, market-cap, listing-age, or fundamental eligibility filter. |
| UNI-005 | P0 | Store canonical symbol, provider symbol, name, exchange, instrument type, status, tradability metadata, and first/last observed dates. |
| UNI-006 | P0 | Preserve symbols containing punctuation or share-class notation without assuming six numeric characters. |
| UNI-007 | P0 | Snapshot the eligible universe for every completed synchronization date. |
| UNI-008 | P0 | Track symbol changes, delistings, relistings, and provider identifier changes without rewriting historical identity. |
| UNI-009 | P0 | Log and report instruments rejected because provider metadata cannot be mapped to an allowed type or venue. |
| UNI-010 | P1 | Support importing point-in-time universe history if Alpaca later exposes or another provider supplies it. |

"No filter" applies to product eligibility. A provider record that cannot be
identified as one of the confirmed venues and types is not eligible until its
metadata is resolved.

### 7.3 Market-data ingestion

| ID | Priority | Requirement |
|---|---|---|
| DAT-001 | P0 | Use Alpaca's official API for initial U.S. market data. |
| DAT-002 | P0 | Ingest completed regular-session daily OHLCV bars from 2016 onward where available. |
| DAT-003 | P0 | Request a feed appropriate for completed end-of-day screening and record the feed used with each ingestion run. |
| DAT-004 | P0 | Do not treat an incomplete current-session bar as final. |
| DAT-005 | P0 | Normalize timestamps to an exchange session date while retaining the source timestamp and timezone. |
| DAT-006 | P0 | Store raw OHLCV separately from derived or adjusted values. |
| DAT-007 | P0 | Ingest available splits, dividends, mergers, symbol changes, and other relevant corporate actions. |
| DAT-008 | P0 | Produce split-adjusted OHLC for price-signal calculation and a total-return series for performance analysis. |
| DAT-009 | P0 | Calculate dollar volume from a documented price and volume definition; do not retain the ambiguous A-share `turnover` meaning. |
| DAT-010 | P0 | Make downloads idempotent using stable instrument, session, provider, feed, and bar-frequency keys. |
| DAT-011 | P0 | Retry transient failures with bounded exponential backoff and jitter. |
| DAT-012 | P0 | Respect provider rate limits and pagination without dropping later symbols. |
| DAT-013 | P0 | Record run start/end times, requested date range, symbols requested, rows received, retries, failures, and data-quality findings. |
| DAT-014 | P0 | Resume a partial synchronization without reprocessing successful immutable pages unnecessarily. |
| DAT-015 | P0 | Detect duplicate bars, invalid OHLC relationships, negative values, impossible volumes, missing session dates, and inconsistent adjustment factors. |
| DAT-016 | P0 | Quarantine suspect records rather than silently coercing them into valid data. |
| DAT-017 | P1 | Reconcile a configurable sample of stored bars against a fresh provider response. |
| DAT-018 | P2 | Permit another provider implementation without changing strategy code or the canonical schema. |

Use IEX only in development/tests, a 150-minute finalization delay, a five-session
bar re-fetch window, and a 63-session corporate-action window. Commercial
production requires written SIP rights.

### 7.4 Exchange calendar

| ID | Priority | Requirement |
|---|---|---|
| CAL-001 | P0 | Use an exchange-aware calendar for NYSE and Nasdaq sessions in `America/New_York`. |
| CAL-002 | P0 | Handle weekends, U.S. market holidays, early closes, and daylight-saving transitions. |
| CAL-003 | P0 | Determine the latest completed session from the calendar and provider state, not `date.today()` alone. |
| CAL-004 | P0 | Run screening at most once per completed session unless explicitly reprocessed. |
| CAL-005 | P0 | Associate every result with an exchange session date and data cutoff timestamp. |

The provider exchange calendar is authoritative. Normal targets are 18:30 ET
sync, 18:45 ET screen, and 19:00 ET digest, adjusted from the official close on
early-close sessions by the same 150-minute finalization rule.

### 7.5 Strategy framework

| ID | Priority | Requirement |
|---|---|---|
| STR-001 | P0 | Define a common strategy interface independent of Alpaca, SQLite, and email. |
| STR-002 | P0 | Provide each strategy a point-in-time data view that cannot read future sessions. |
| STR-003 | P0 | Store strategy ID, version, parameters, universe snapshot, input cutoff, output symbols, and diagnostic counts for every run. |
| STR-004 | P0 | Make all thresholds and lookback windows configurable and type-validated. |
| STR-005 | P0 | Reject parameter sets that cannot be evaluated with the supplied history. |
| STR-006 | P0 | Return structured selections with symbol, rank, matched conditions, and relevant metrics rather than only a list of strings. |
| STR-007 | P0 | Produce deterministic output for identical data and configuration. |
| STR-008 | P0 | Isolate a failure in one symbol or strategy without invalidating unrelated completed results. |
| STR-009 | P0 | Distinguish "no selections" from "strategy failed" in storage, logs, and email. |

#### 7.5.1 Moving-average and volume strategy

- Preserve the existing moving-average crossover concept.
- Calculate moving averages from split-adjusted closing prices.
- Calculate volume confirmation from a documented rolling-volume series.
- Make short window, long window, crossover convention, and volume multiplier configurable.
- Research baseline: fast 20 sessions, slow 50 sessions, volume window 20,
  and minimum volume multiple 1.5. These are experimental, not production claims.

#### 7.5.2 High tight flag strategy

- Preserve strong momentum, tight consolidation, high-level support, and volume contraction concepts.
- Make all windows, ratios, and volume multipliers configurable.
- Define missing-data and zero-price behavior explicitly.
- Research baseline: 40-session momentum ratio 2.0; 10-session consolidation
  ratio at most 1.25; high retention at least 0.75; 20-session volume baseline;
  current volume ratio at most 0.80.

#### 7.5.3 Turtle breakout strategy

- Preserve the preceding-high breakout and bullish confirmation concepts.
- Replace A-share turnover-rate-derived free-float market capitalization.
- Use configurable dollar-volume liquidity confirmation.
- Permit ranking by dollar volume or breakout strength without requiring a
  separate market-capitalization entitlement.
- Research baseline: 55-session breakout, no strategy-specific dollar-volume
  eligibility floor, bullish-body confirmation, ranked by breakout strength.

#### 7.5.4 Relative Price Strength breakout strategy

- Rank the complete eligible universe for the relevant point-in-time session.
- Include common stocks, ETFs, and ADRs because no strategy-specific exclusion was authorized.
- Make return window, percentile threshold, breakout window, and proximity threshold configurable.
- Record universe size and valid-observation count with every rank.
- Define deterministic behavior for ties.
- Research baseline: 252-session return, 90th percentile minimum,
  63-session breakout window, and 0.98 minimum high proximity.

#### 7.5.5 Gap-up shakeout strategy

This replaces the China-specific limit-up shakeout strategy.

- Detect a configurable prior-session or multi-session upward price shock.
- Require a configurable bearish or reversal candle condition.
- Require configurable elevated volume.
- Require price to hold a configurable reference level.
- Do not call the event "limit up" unless actual U.S. LULD event data supports that claim.
- Research baseline: 4%–20% gap, volume ratio at least 0.75, support tolerance
  5%, bearish shakeout required, and a five-session research holding period.

#### 7.5.6 Uptrend shock-reversal strategy

This replaces the China-specific uptrend limit-down strategy.

- Require a configurable trend definition.
- Detect a configurable downside shock expressed as percentage return, ATR multiple,
  statistical deviation, or a versioned combination.
- Require configurable volume confirmation.
- Do not call the event "limit down" based solely on a daily percentage decline.
- Research baseline: 50/200-session trend, 14-session ATR, downside return at
  least 8%, shock at least 2.0 ATR, 20-session volume baseline, volume multiple
  at least 1.5, close location at least 0.75, and five-session holding period.

### 7.6 SEC offering monitor

| ID | Priority | Requirement |
|---|---|---|
| SEC-001 | P0 | Use official SEC EDGAR sources for filing discovery and retrieval. |
| SEC-002 | P0 | Identify the client with the operator's organization and contact information. |
| SEC-003 | P0 | Enforce the SEC fair-access ceiling of no more than ten requests per second across all workers. |
| SEC-004 | P0 | Monitor all eligible common-stock and ADR issuers that can be mapped to an SEC CIK. |
| SEC-005 | P0 | Apply the approved tiered candidate-form inventory below. |
| SEC-006 | P0 | Detect candidate public offerings, registered direct offerings, PIPE transactions, private placements, at-the-market programs, secondary offerings, and material amendments where the filing supports that classification. |
| SEC-007 | P0 | Report every potentially relevant filing; do not suppress based on confidence. |
| SEC-008 | P0 | Assign a classification, confidence indicator, matched evidence, and reason for relevance to every alert. |
| SEC-009 | P0 | Label uncertain classifications as uncertain rather than presenting them as facts. |
| SEC-010 | P0 | Deduplicate using SEC accession number and alert revision state. |
| SEC-011 | P0 | Preserve source URL, filing timestamp, accepted timestamp, form, issuer, CIK, mapped symbol, and extracted evidence. |
| SEC-012 | P0 | Keep filing alerts separate from technical-strategy selections while permitting a combined daily digest. |
| SEC-013 | P0 | Continue from the last successfully processed accession after restart. |
| SEC-014 | P1 | Detect amendments or later filings that materially change a previously reported offering. |

Tier A covers S-1/S-1-A, S-3/S-3-A/S-3ASR, F-1/F-1-A,
F-3/F-3-A/F-3ASR, 424B1/2/3/4/5/7/8, FWP, POS AM, POSASR, EFFECT, and RW.
Tier B covers D/D-A, 1-A/1-A-A/1-K/1-SA, 253G1–4, S-8/S-8 POS, and
S-4/F-4 plus amendments. Tier C covers 8-K/8-K-A, 6-K/6-K-A,
10-Q/10-Q-A, 10-K/10-K-A, PRE 14A, and DEF 14A. Configuration uses the
SEC's literal slash form names where applicable.

The classifier categories are registered direct, shelf, shelf takedown,
at-the-market, private placement, convertible/warrant, employee issuance,
merger/exchange, Regulation A, amendment, effectiveness, withdrawal, and
uncertain. Explicit direct-offering evidence is immediate; other candidates are
batched for the beta digest. Confidence is informational and never suppresses a
candidate. Poll every ten minutes 06:00–22:00 ET weekdays and hourly otherwise.

### 7.7 Email notifications

| ID | Priority | Requirement |
|---|---|---|
| NOT-001 | P0 | Email is the default and required MVP notification channel. |
| NOT-002 | P0 | Support both HTML and readable plain-text alternatives. |
| NOT-003 | P0 | Route recipients by strategy or filing-alert category using centrally managed configuration. |
| NOT-004 | P0 | Include strategy name/version, session date, data cutoff, selected symbols, company names, matched metrics, and direct security links. |
| NOT-005 | P0 | SEC emails must include filing form, classification, uncertainty indicator, accepted timestamp, evidence summary, and direct SEC link. |
| NOT-006 | P0 | Never send raw market datasets as notification attachments. |
| NOT-007 | P0 | Generate an idempotency key and prevent duplicate delivery to the same recipient for the same result revision. |
| NOT-008 | P0 | Retry transient email failures without duplicating successful recipients. |
| NOT-009 | P0 | Record recipient, template version, send attempt, provider response, final status, and timestamps without storing credentials. |
| NOT-010 | P0 | Clearly distinguish delayed, partial, stale, failed, and complete screening runs. |
| NOT-011 | P0 | Escape or sanitize provider and filing text before rendering HTML. |
| NOT-012 | P1 | Add WhatsApp through the official Cloud API with separate opt-in and suppression evidence. |

Use Brevo Free SMTP with STARTTLS on port 587 for the private beta, a 300-attempt
daily safety cap, at most 25 initial recipients, and four total attempts delayed
60, 300, and 1,800 seconds. The actual sender domain remains an external input
and must pass SPF, DKIM, and DMARC checks. Technical results are one daily digest.
Private-beta unsubscribe requests are processed within one business day; public
launch requires hosted one-click unsubscribe and synchronized suppression.

### 7.8 Subscriber management

| ID | Priority | Requirement |
|---|---|---|
| SUB-001 | P0 | Maintain a centrally managed list of subscriber email addresses and enabled alert categories. |
| SUB-002 | P0 | Validate and normalize addresses before activation. |
| SUB-003 | P0 | Support activation, deactivation, and category changes without editing source code. |
| SUB-004 | P0 | Keep an auditable history of subscription-state changes. |
| SUB-005 | P0 | Prevent deactivated recipients from receiving later alerts. |
| SUB-006 | P0 | Restrict access to subscriber data to authorized local operators. |
| SUB-007 | P1 | Support import/export without exposing secrets or unrelated operational data. |

Activation requires timezone-aware double-opt-in evidence and a privacy-policy
version. Deactivation requires timestamped reason and prevents future sends.
Subscriber PII is retained while active plus 30 days; consent/unsubscribe
evidence is retained seven years and suppression evidence indefinitely where
lawful. Billing, privacy notices, and public compliance language remain counsel
and commercial-launch gates.

### 7.9 Backtesting

| ID | Priority | Requirement |
|---|---|---|
| BT-001 | P0 | Backtest every implemented technical strategy against available data from 2016 onward. |
| BT-002 | P0 | Reuse production strategy logic rather than maintain a second calculation implementation. |
| BT-003 | P0 | Accept configurable start/end dates, universe, strategy version, parameters, benchmark, rebalance rule, holding rule, execution convention, capital, position sizing, costs, and slippage. |
| BT-004 | P0 | Reject any run whose execution timing would use data unavailable at the simulated decision time. |
| BT-005 | P0 | Support at least next-session-open and next-session-close execution conventions. |
| BT-006 | P0 | Model configurable fixed fees, per-share fees, percentage fees, minimum fees, and slippage. |
| BT-007 | P0 | Use SPY as the baseline benchmark and VTI as a sensitivity benchmark while keeping the benchmark configurable. |
| BT-008 | P0 | Use split-adjusted signals and total-return-aware performance series according to a documented policy. |
| BT-009 | P0 | Include dividends, splits, symbol changes, and delistings where source data permits. |
| BT-010 | P0 | Use point-in-time universe snapshots where available and identify every interval that falls back to a current or incomplete universe. |
| BT-011 | P0 | Never label a backtest survivorship-bias-free unless point-in-time and delisted-security evidence supports that claim. |
| BT-012 | P0 | Support anchored or rolling walk-forward evaluation with configurable training, validation, test, and step windows. |
| BT-013 | P0 | Preserve untouched out-of-sample results separately from parameter-selection results. |
| BT-014 | P0 | Calculate total return, annualized return, annualized volatility, Sharpe ratio, Sortino ratio, maximum drawdown, Calmar ratio, hit rate, exposure, turnover, trade count, average holding period, and benchmark-relative return. |
| BT-015 | P0 | Report the risk-free-rate assumption and permit it to be configured. |
| BT-016 | P0 | Export a machine-readable run manifest, trades, positions, equity curve, metrics, warnings, and failures. |
| BT-017 | P0 | Produce a human-readable report that separates in-sample, validation, and out-of-sample results. |
| BT-018 | P0 | Make every run reproducible from stored data version, code version, configuration, and random seed where randomness exists. |
| BT-019 | P1 | Support parameter sweeps without selecting a winner from out-of-sample results. |
| BT-020 | P1 | Support comparison of multiple strategy versions under identical assumptions. |

The approved research baseline is SPY with VTI sensitivity, $100,000 initial
capital, next-session-open execution, daily rebalance, and 10% of initial capital
per position. Costs are $0 fixed, $0.005/share, 0% notional, and $1 minimum per
side. Baseline slippage is 10 bps with 25/50 bps stress runs; the disclosed
constant risk-free rate is 3%. Rolling windows are 756/252/252/252 sessions and
selection maximizes Calmar ratio. MA, HTF, Turtle, and RPS hold 20 sessions;
Gap and Shock hold five. Official runs fail on missing exit data; `mark_zero` is
a separately labeled stress policy.

### 7.10 Operator commands and scheduling

| ID | Priority | Requirement |
|---|---|---|
| OPS-001 | P0 | Provide commands for configuration validation, universe sync, daily sync, historical backfill, screening, SEC sync, notification retry, backtest, health check, and data audit. |
| OPS-002 | P0 | Commands must return zero only when their requested operation reaches a defined successful state. |
| OPS-003 | P0 | Support non-interactive execution by the local operating system's scheduler. |
| OPS-004 | P0 | Prevent overlapping runs that could corrupt state or duplicate notifications. |
| OPS-005 | P0 | Permit an operator to reprocess a session without automatically resending previously successful notifications. |
| OPS-006 | P0 | Provide dry-run modes for screening and notification delivery. |
| OPS-007 | P0 | Display a concise end-of-run summary and persist detailed diagnostics. |
| OPS-008 | P1 | Support export/import for backup and machine migration. |

Production uses a dedicated Ubuntu 24.04.4 LTS mini-PC and systemd; Windows is a
development environment. Nightly encrypted consistent SQLite backups retain 7
daily, 5 weekly, and 12 monthly copies locally and offsite. RPO is 24 hours, RTO
four hours, and restoration is tested quarterly. The exact encrypted offsite
destination is an operator deployment input.

## 8. Proposed system architecture

The following architecture is a requirement boundary, not a mandate for specific
class or filename names:

```text
Alpaca API ───────┐
                  ├── Provider adapter ── Normalization ── Canonical storage
SEC EDGAR ────────┘                                      │
                                                         ├── Strategy engine
Exchange calendar ───────────────────────────────────────┤
                                                         ├── Backtest engine
                                                         └── Result store
                                                                  │
Subscriber configuration ── Email notifier ──────────────────────┘
```

### 8.1 Required component boundaries

1. **Market provider adapter:** Alpaca authentication, pagination, rate limiting,
   instrument discovery, bars, and corporate actions.
2. **SEC provider adapter:** EDGAR discovery, retrieval, fair-access limiting, and
   accession tracking.
3. **Normalization layer:** provider records to versioned canonical models.
4. **Calendar service:** completed sessions, holidays, and early closes.
5. **Persistence layer:** transactions, migrations, repositories, backups, and
   data-version identifiers.
6. **Strategy engine:** point-in-time strategy inputs and structured selections.
7. **Backtest engine:** portfolio simulation, costs, benchmarks, evaluation splits,
   and reports.
8. **Notification layer:** email first, generic channel interface second.
9. **Operator interface:** commands, scheduling integration, health, and audit.

No strategy may call Alpaca, SEC EDGAR, email, or raw SQL directly.

### 8.2 Provider interface capabilities

The market provider interface must represent:

- Instrument listing and metadata.
- Daily bars for multiple symbols and date ranges.
- Corporate actions.
- Provider/feed identity.
- Pagination and continuation state.
- Rate-limit state where available.
- Source-specific symbol mapping.

Provider-specific payloads may be retained for audit, but downstream strategy code
must consume canonical models.

## 9. Canonical data model

SQLite with application-controlled versioned migrations is the local MVP store.
The canonical model must support the following entities.

### 9.1 Instrument

- Stable internal instrument ID.
- Canonical symbol and provider symbol.
- Issuer/entity name.
- Exchange and provider exchange code.
- Instrument type: common stock, ETF, or ADR.
- Currency.
- Active/inactive and tradability metadata.
- CIK where available.
- First observed, last observed, listing, and delisting dates where available.
- Raw provider metadata and source timestamp.

### 9.2 Universe snapshot

- Session date.
- Instrument ID.
- Eligibility state and reason.
- Provider/catalog version.
- Observation timestamp.

### 9.3 Daily bar

- Instrument ID and session date.
- Raw open, high, low, close, and volume.
- Split-adjusted open, high, low, and close.
- Total-return adjusted close.
- Dollar volume and its calculation convention.
- Provider, feed, source timestamp, ingestion run, and adjustment version.
- Data-quality state.

### 9.4 Corporate action

- Instrument ID.
- Action type.
- Declaration, ex, record, payable, and effective dates where available.
- Ratio or cash amount.
- Currency.
- Provider identifiers and raw payload reference.

### 9.5 Strategy run and selection

- Run ID, strategy ID/version, parameters, code version, and data version.
- Universe snapshot and input cutoff.
- Start/end/status timestamps.
- Selection instrument, rank, matched conditions, and metrics.
- Failure and warning records.

### 9.6 SEC filing and classification

- Accession number, CIK, form, filing/accepted timestamps, source URL, and issuer.
- Mapped instrument ID where available.
- Candidate classification, confidence indicator, evidence, and classifier version.
- Alert status and revision history.

### 9.7 Subscriber and delivery

- Subscriber ID and normalized address.
- Active state and alert-category subscriptions.
- State-change audit records.
- Delivery ID, idempotency key, result/filing reference, template version, attempts,
  provider response, timestamps, and final status.

### 9.8 Backtest run

- Full run manifest and code/data/configuration versions.
- Simulation assumptions and evaluation windows.
- Orders/trades, positions, cash, equity curve, metrics, benchmark, warnings, and
  bias-control status.

## 10. Non-functional requirements

### 10.1 Correctness

- All price calculations must state whether they use raw, split-adjusted, or
  total-return data.
- No strategy or backtest may access a record published after its simulation cutoff.
- Re-running identical input must produce identical selections and reports.
- Database migrations must preserve or explicitly transform historical semantics.

### 10.2 Reliability

- All externally sourced operations must be retryable and resumable.
- A process termination must not leave a run marked successful or partially commit
  an internally inconsistent batch.
- Notification retries must be recipient-aware and idempotent.
- Health checks must detect stale market data, stale SEC polling, failed schedules,
  exhausted credentials, and unavailable email transport.

The monthly service targets are 99% of completed-session digests by 20:00 ET and
95% of monitored direct SEC alerts within 20 minutes, subject to the exclusions
in Section 2.1.

### 10.3 Performance

- The system must process the complete confirmed universe without eligibility
  filtering.
- The implementation must use provider-supported batching and vectorized strategy
  calculations where practical.
- Backtests must expose progress and support cancellation without corrupting stored results.

The private beta is capped at 25 subscribers and 300 provider attempts/day.
Runtime baselines must be measured on the selected mini-PC before broader beta;
the 20:00 ET digest SLO is the binding end-to-end performance target.

### 10.4 Security and privacy

- Secrets must remain outside source control and persisted reports.
- Logs must not contain credentials, subscriber message bodies, or unnecessary
  subscriber information.
- HTML email must treat all external text as untrusted.
- Subscriber exports and backups must be access-controlled.
- Dependencies must remain locked and pass the repository's quality and vulnerability
  review process selected by engineering.

Use an encrypted Ubuntu disk, encrypted local/offsite backups, and a dedicated
least-privilege service account. Section 2.1 defines retention. Public privacy
policy and incident-response language require counsel/operations approval.

### 10.5 Observability

- Use structured logs with run IDs, strategy IDs, instrument IDs, filing accession
  numbers, and delivery IDs where applicable.
- Persist operational run summaries separately from console output.
- Provide counts for requested, received, accepted, quarantined, retried, failed,
  selected, and notified records.
- Never represent missing or partial source data as a valid zero-result strategy run.

### 10.6 Maintainability

- Python 3.12 or later.
- Dependencies and environments managed by uv with a committed lockfile.
- Black formatting, Ruff linting, strict mypy, pytest, and package-build checks.
- Provider and notification adapters covered by contract tests.
- Database schema controlled by versioned migrations.
- Public modules and configuration documented in English.

## 11. Commercial and data-use constraints

The product is centrally operated and commercial/shared. IEX is approved only
for development and automated contract tests. Written rights to an appropriate
SIP feed are a commercial production acceptance criterion.

The system must nevertheless:

- Send derived strategy results and filing summaries, not raw market datasets.
- Record which Alpaca plan/feed supplied each result.
- Avoid claiming rights, warranties, or permissions not explicitly established.
- Keep provider terms and operational limitations documented as a product risk.
- Permit provider replacement if the operating model becomes incompatible with
  provider terms, coverage, quality, or economics.

Legal review, financial-promotion classification, commercial email obligations,
disclaimers, subscriber terms, privacy obligations, and data-use interpretation
remain external professional-advice gates. Engineering must not invent conclusions.

## 12. External source constraints

### 12.1 Alpaca

- Historical stock data is available through Alpaca's market-data API.
- Available feed, recent-data delay, history, coverage, rate limits, and corporate
  actions depend on the account and plan.
- The implementation must inspect API errors and entitlement responses instead of
  assuming access.

References:

- <https://docs.alpaca.markets/us/docs/about-market-data-api>
- <https://docs.alpaca.markets/us/v1.1/docs/historical-stock-data-1>
- <https://docs.alpaca.markets/us/docs/market-data-faq>

### 12.2 SEC EDGAR

- SEC submissions and extracted data are available through official JSON and filing resources.
- Automated access must identify the client and remain within SEC fair-access guidance.
- The total request rate must not exceed ten requests per second.

Reference:

- <https://www.sec.gov/about/developer-resources>

### 12.3 U.S. market structure

The U.S. Limit Up/Limit Down mechanism uses intraday price bands and possible
trading pauses. It is not equivalent to China's fixed daily closing-price limits.
The redesigned daily strategies must not imply equivalence.

References:

- <https://www.finra.org/filing-reporting/trf/limit-uplimit-down-luld-plan>
- <https://www.finra.org/investors/insights/guardrails-market-volatility>

## 13. Testing requirements

### 13.1 Unit tests

- Symbol and instrument normalization.
- Venue/type eligibility without financial filters.
- Calendar/session resolution.
- Bar validation and adjustment calculations.
- Every strategy condition and boundary.
- SEC candidate rules, evidence extraction, and uncertainty labels.
- Cost, slippage, execution, metrics, and walk-forward calculations.
- Email rendering, escaping, routing, and idempotency.

### 13.2 Property tests

- Idempotent ingestion and notification behavior.
- No future data visible to strategy/backtest cutoffs.
- Adjustment invariants around splits and dividends.
- OHLC validation invariants.
- Stable RPS ranking and tie behavior.
- Backtest accounting conservation across cash, positions, costs, and equity.

### 13.3 Contract tests

- Alpaca fixture payloads for instruments, bars, pagination, actions, errors, and rate limits.
- SEC fixture payloads and filing documents.
- Email provider success, transient failure, permanent failure, and partial-recipient outcomes.
- Provider adapters tested without live credentials in the default test suite.

### 13.4 Integration tests

- Temporary database from schema initialization through screen results.
- Backfill interruption and resume.
- Daily sync through strategy run and dry-run email.
- SEC polling through classification and dry-run email.
- Database migration from the current schema.

### 13.5 Data-quality tests

- Compare a documented sample of symbols and sessions with Alpaca responses.
- Include split, dividend, symbol-change, ETF, ADR, inactive, and punctuation-bearing symbols.
- Verify missing-bar behavior across holidays and listing boundaries.

### 13.6 Backtest validation

- Synthetic price series with analytically known outcomes.
- Tests proving same-close execution cannot use a close-derived signal unless the
  convention explicitly models an available decision point.
- Tests proving parameter selection does not inspect protected out-of-sample results.
- Benchmark and cost-model reconciliation.

## 14. Release phases

### Phase 0: decisions and migration design

- Record the approved baseline and external gates in Sections 2.1 and 18.
- Freeze canonical schema and adjustment semantics.
- Define migration and removal behavior for A-share data and configuration.
- Create an explicit product-risk record for commercial data use.

### Phase 1: U.S. data foundation

- Alpaca adapter.
- Instrument catalog and universe snapshots.
- Calendar service.
- Canonical bars and corporate actions.
- Historical backfill from 2016.
- Data audit and resumable daily sync.

### Phase 2: strategies and results

- Common strategy interface.
- MA/volume, high tight flag, Turtle, and RPS ports.
- Gap-up shakeout and uptrend shock-reversal implementations after parameter decisions.
- Structured result storage.

### Phase 3: SEC monitoring and email

- SEC adapter, candidate detection, classification, and accession state.
- Subscriber configuration.
- Email rendering, routing, idempotency, audit, and dry runs.

### Phase 4: backtesting and model review

- Simulation engine and bias controls.
- Metrics, benchmarks, reports, and walk-forward evaluation.
- Strategy parameter review using separated in-sample and out-of-sample results.

### Phase 5: operational release

- Local scheduling, locking, health checks, backups, recovery drills, and runbooks.
- Commercial templates and counsel-approved compliance decisions.
- End-to-end production rehearsal without subscriber delivery.
- Controlled subscriber launch.

## 15. MVP acceptance criteria

The MVP is acceptable only when all of the following are evidenced:

1. The repository contains no runtime dependency on baostock, AKShare, Xueqiu,
   six-digit A-share symbol rules, or China-specific price-limit terminology.
2. The system can discover and store every Alpaca instrument matching the confirmed
   venue and type rules, with no unauthorized eligibility filter.
3. A 2016-forward backfill completes or reports every unavailable interval and symbol.
4. Re-running the backfill produces no duplicate canonical records.
5. Corporate actions and adjustment semantics pass documented sample reconciliation.
6. The service runs each enabled strategy for a completed session and stores a
   structured success, zero-result, partial, or failure state.
7. The two redesigned U.S. volatility strategies have approved definitions and
   versioned parameters.
8. The SEC monitor reports every filing matched by the approved candidate inventory,
   including uncertain matches, without duplicate accession alerts.
9. Email dry runs and live test recipients prove routing, HTML/plain text, escaping,
   retries, and idempotency.
10. Backtests include explicit execution, costs, slippage, benchmark, universe,
    corporate-action, and bias-control disclosures.
11. Walk-forward and untouched out-of-sample reports can be reproduced from a run manifest.
12. Restart, provider failure, partial email failure, stale data, and overlapping-run
    recovery scenarios pass.
13. Black, Ruff, strict mypy, pytest, package build, and isolated package smoke tests pass.
14. Operator documentation covers installation, configuration, secrets, backfill,
    scheduling, health, backup, restore, retry, and incident diagnosis.
15. Every baseline product decision is recorded, and every external launch gate
    in Section 18 has evidence or is explicitly outside the private-beta scope.

## 16. Success metrics

The product will measure the following. Operational targets are defined below;
research and commercial measures establish baselines during the private beta:

### Product metrics

- Active subscribers.
- Delivery success rate.
- Subscriber activation and deactivation counts.
- Strategy email open/click measures only if explicitly authorized and implemented.
- Subscriber retention and cancellation reasons once commercial onboarding is defined.

### Operational metrics

- Percentage of completed sessions synchronized successfully.
- Time from provider bar finalization to completed screening.
- Percentage of universe with valid current bars.
- Data-quality quarantine rate.
- Duplicate notification count.
- SEC polling lag and filing-to-alert latency.
- Mean time to detect and recover from failed runs.

### Research metrics

- Strategy coverage and selection frequency.
- Out-of-sample return and drawdown relative to configured benchmarks.
- Turnover and sensitivity to costs/slippage.
- Parameter stability across walk-forward windows.
- Percentage of backtest periods with complete point-in-time universe evidence.

No return metric alone constitutes product success or a performance promise.

## 17. Risks and mitigations

| Risk | Impact | Required mitigation |
|---|---|---|
| Commercial/shared use conflicts with provider terms or plan | Provider interruption or legal exposure | Do not redistribute raw data; record feed/plan; retain replaceable adapter; keep as explicit product risk. |
| Alpaca history begins around 2016 | Limited regime coverage | Disclose range; permit future provider/import adapter. |
| Point-in-time delisted universe is incomplete | Survivorship-biased backtests | Daily snapshots from launch; ingest inactive assets where possible; label limitations per run. |
| No eligibility filters creates many illiquid or unusual selections | Low-quality or hard-to-trade signals | Preserve confirmed universe rule; expose liquidity metrics in results; do not silently filter. |
| ETFs and ADRs rank with common stocks | Mixed economic populations | Preserve confirmed universe; report type; future segmentation requires a PRD decision. |
| Corporate-action corrections change history | Non-reproducible signals/backtests | Raw data, adjustment versions, correction windows, immutable run manifests. |
| U.S. volatility logic is incorrectly treated as China's daily limits | Invalid strategy thesis | New names, configurable U.S. definitions, backtest approval, no unsupported LULD claim. |
| SEC form matching creates false positives | Alert fatigue | Send all candidates as required, but include classification, uncertainty, evidence, and category routing. |
| SEC text extraction misses offering language | False negatives | Versioned form inventory and rules, fixture corpus, audit counts, later amendments. |
| Local machine is offline or asleep | Missed runs and delayed alerts | Health checks, catch-up commands, scheduler monitoring, startup resume, backup/recovery runbook. |
| Email provider rejects or rate-limits sends | Delayed notifications | Recipient-aware retries, idempotency, status audit, configurable batching. |
| Commercial subscriber data is mishandled | Privacy and trust impact | Minimize stored data, restrict local access, define retention and compliance before launch. |
| Backtest overfitting | Misleading strategy expectations | Walk-forward evaluation, untouched out-of-sample set, parameter/version history, cost sensitivity. |

## 18. Remaining release inputs and future decisions

The product decisions needed for the development/private-beta baseline are
approved. The following items require real deployment information or third-party
evidence and cannot be fabricated in the repository.

### Commercial production gates

1. Written Alpaca/SIP commercial data rights and the credentialed production plan.
2. Legal organization name, SEC contact address, product owner, operations owner,
   real sender domain, support address, and public security-link host.
3. SPF, DKIM, and DMARC verification plus Brevo credentialed acceptance tests.
4. Hosted one-click unsubscribe and provider suppression synchronization before
   any public launch.
5. Securities, privacy, terms, financial-promotion, and commercial-email counsel
   sign-off; engineering records the outcome but does not provide the opinion.
6. Named encrypted offsite backup destination and a successful restore-test record.
7. Billing provider, tax handling, refund terms, and final validated prices before
   accepting payment.
8. Credentialed acceptance evidence for OpenFIGI, Alpaca, Nasdaq, SEC, and Brevo.

### P1 product decisions

1. WhatsApp Cloud API business account, regions, templates, consent UX, and pricing trigger.
2. Subscriber import/export format and hosted administration scope.
3. Additional market-data/reference provider priority and commercial evaluation.
4. Cloud/container migration trigger and high-availability target.
5. Whether strategy parameter sets graduate from experimental research baselines.

## 19. Traceability summary

| Confirmed decision | Primary requirement coverage |
|---|---|
| U.S. only | Sections 2, 5, 15 |
| Commercial/shared, centrally operated | Sections 6, 7.8, 11 |
| Alpaca | Sections 7.3, 12.1 |
| EOD first | Sections 4, 7.3, 7.4 |
| Screener/notifier only | Sections 4, 5 |
| NYSE/Nasdaq common stocks, ETFs, ADRs | Section 7.2 |
| No filters | UNI-004 and risk register |
| Redesign price-limit strategies | Sections 7.5.5 and 7.5.6 |
| SEC monitoring in first release | Section 7.6 |
| Email first, alternatives later | Section 7.7 |
| Local machine | Section 7.10 |
| History since 2016 sufficient | Sections 7.3 and 7.9 |
| Flexible rigorous backtesting | Section 7.9 |
| Alert every relevant SEC filing | SEC-007 and Section 7.6 |

## 20. Approval

Approval confirms the requirements and Section 2.1 baseline. It does not stand in
for the external contracts, credentials, identities, or professional advice in
Section 18.

| Role | Name | Decision | Date |
|---|---|---|---|
| Product owner | Name not provided | Baseline decisions confirmed in discussion | 2026-08-18 |
| Engineering owner | Name not provided | Pending implementation acceptance | — |
| Operations owner | Name not provided | Pending deployment acceptance | — |
