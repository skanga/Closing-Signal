# Missing strategy research and priorities

| Field | Value |
|---|---|
| Product | Closing Signal |
| Date | 2026-08-19 |
| Purpose | Prioritize important strategy families missing from the current six-strategy suite |
| Status | Research recommendation; not an approved performance claim |

## Executive summary

The current six strategies are concentrated in price momentum, breakouts, trend
continuation, and reversal candles. The largest opportunity is not another chart
pattern; it is adding orthogonal fundamental, event-driven, and mean-reversion
signals.

| Priority | Strategy | Value to Closing Signal | Data and effort |
|---|---|---|---|
| 1 | Earnings momentum and post-earnings-announcement drift | Strong event-driven complement to technical signals | SEC XBRL and 8-K; high effort |
| 2 | Quality at a reasonable price | Finds financially strong companies without buying quality at any price | SEC XBRL plus market capitalization; high effort |
| 3 | Canonical 12–1 momentum | More research-standard than the existing RPS breakout | Existing bars; low effort |
| 4 | Short-term residual reversal | Diversifies the trend-heavy strategy suite | Existing bars; medium effort |
| 5 | Insider cluster buying | Explainable corporate-event notification | SEC Form 4; medium effort |
| 6 | Low-risk and lottery-avoidance composite | Improves risk-adjusted candidate quality | Existing bars, optionally fundamentals; medium effort |

## 1. Earnings momentum and post-earnings-announcement drift

This is the strongest recommended addition.

A practical version would combine:

- standardized unexpected earnings based on year-over-year EPS changes;
- revenue acceleration;
- earnings-announcement gap and abnormal volume;
- positive earnings direction sustained for multiple quarters;
- filing timestamps enforced point-in-time;
- a 20–60-session post-announcement observation window.

Research finds that earnings-surprise measures can subsume much of price
momentum's predictive information. [Novy-Marx's fundamental-momentum
paper](https://www.nber.org/papers/w20984) is particularly relevant. The SEC
provides real-time submissions and XBRL financial facts without an API key,
making a free implementation feasible. [SEC EDGAR API
documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)

Caveats:

- PEAD has weakened over time.
- Transaction costs materially reduce implementable returns.
- XBRL normalization and point-in-time accounting are difficult.
- Analyst-consensus surprises require another, probably commercial, dataset.

A long-history study still finds PEAD but also documents its decline;
transaction-cost research shows that much of the apparent opportunity can occur
in expensive-to-trade securities. [Long-history PEAD
study](https://papers.ssrn.com/sol3/Delivery.cfm/4373735.pdf?abstractid=4373735&mirid=1),
[transaction-cost
study](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1024185)

## 2. Quality at a reasonable price

Build a composite rather than separate value and quality screens:

- gross and operating profitability;
- free-cash-flow yield;
- earnings yield;
- accrual quality;
- conservative asset growth;
- debt and interest coverage;
- share dilution;
- valuation relative to sector peers.

Profitability has historically provided information comparable and complementary
to book-to-market. [Gross-profitability
research](https://users.nber.org/~confer/2010/APf10/Novy-Marx.pdf) Quality
composites covering profitability, growth, safety, and management quality have
also produced significant historical risk-adjusted spreads. [Quality Minus
Junk](https://conference.nber.org/confer/2013/APf13/Frazzini_Pedersen_Asness.pdf)

This should preferably be combined with momentum:

> financially strong + reasonably valued + positive price trend

That combination is intended to reduce value traps and exposure to speculative
momentum names.

The difficult part is data normalization, particularly for IFRS-reporting ADRs.
ETFs should be explicitly marked `not_applicable`, because corporate
profitability metrics are not meaningful for a fund. This applicability policy
requires product approval and must not become a silent universe exclusion.

## 3. Upgrade RPS to canonical 12–1 momentum

The current RPS strategy uses a 252-session return including recent performance
and requires breakout proximity. It is close to momentum but does not implement
the canonical research definition.

Add a distinct implementation with:

- return from approximately session −252 through −21;
- the most recent month skipped to avoid short-term reversal contamination;
- cross-sectional ranking;
- optional sector-neutral ranks;
- positive absolute trend confirmation;
- optional volatility-scaled ranking;
- monthly rebalance and a 20-session holding period.

The Kenneth French library maintains momentum portfolios based on prior 2–12
month returns, alongside reversal, profitability, investment, and value research
portfolios. [Kenneth French Data
Library](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html)

Add a momentum-risk overlay as part of this work. Momentum can suffer severe
crashes after market declines, during elevated volatility, and during sharp
rebounds. [Momentum Crashes](https://www.nber.org/papers/w20439)

This is the fastest high-value addition because it needs no new provider.

## 4. Short-term residual reversal

This would diversify the strongly trend-oriented suite.

A robust version should use:

- one-, three-, and five-session abnormal returns;
- return residualized against SPY and preferably sector performance;
- ATR-normalized price displacement;
- abnormal volume;
- close location and overnight gap;
- a corporate-event/news veto;
- a two- to five-session holding period.

Research interprets short-term reversals partly as compensation for supplying
liquidity, with strength depending on volatility and turnover. [Recent reversal
research](https://www.nber.org/papers/w30917), [liquidity-state
evidence](https://www.nber.org/papers/w17653)

Transaction costs are the principal risk. This should not be implemented as a
simple `RSI < 30` rule. It needs realistic spread/slippage stress and should show
liquidity risk in every alert.

## 5. Insider cluster buying

Extend the SEC infrastructure to Forms 3, 4, and 5, focusing on:

- transaction code `P` open-market purchases;
- at least two distinct officers/directors purchasing within a short window;
- dollar value relative to existing holdings;
- repeated purchases;
- exclusion of grants, gifts, option exercises, automatic plans, and
  administrative transactions;
- stronger ranking when price trend or valuation also confirms.

Historical research found that insider purchases were informative while sales
generally were not. [Insider-purchase
performance](https://www.nber.org/papers/w6913), [cross-sectional
evidence](https://www.nber.org/papers/w6656) Form 4 generally must be filed within
two business days, making it operationally suitable for notifications. [SEC
reporting rule](https://www.sec.gov/files/rules/other/34-46313.htm)

Treat this as an alerting signal, not a standalone buy recommendation. Recent
research warns that overlapping observations can exaggerate the statistical
significance of cluster buying. [Contrarian cluster-buying
evidence](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6883638)

## 6. Low-risk and lottery-avoidance composite

Useful components include:

- low beta versus SPY;
- low idiosyncratic volatility;
- stable downside deviation;
- low maximum single-day return over the previous month;
- moderate leverage and positive profitability;
- positive long-term trend.

Low-beta assets have historically produced better risk-adjusted results than
high-beta assets in several markets, although the full academic strategy uses
leverage and shorting that would not fit this product. [Betting Against
Beta](https://www.nber.org/papers/w16601) Stocks with extreme recent one-day
gains—lottery stocks—have historically had lower subsequent returns. [MAX-effect
research](https://www.nber.org/papers/w14804)

For Closing Signal, use this primarily as a ranking or warning overlay.

## Recommended roadmap

1. Implement canonical 12–1 momentum and its regime-risk overlay first.
2. Build a point-in-time SEC XBRL fundamentals service.
3. Add earnings momentum/PEAD.
4. Add quality at a reasonable price.
5. Add short-term residual reversal with aggressive cost testing.
6. Extend SEC ingestion to insider cluster buying.
7. Add low-risk/lottery avoidance as a cross-strategy ranking overlay.

If only three strategies are funded, choose:

1. earnings momentum;
2. quality at a reasonable price;
3. short-term residual reversal.

Together, these address the current suite's main weakness: almost every existing
strategy depends on prices continuing in roughly the same direction.

## Research and release standard

No strategy should be described as powerful, validated, or suitable for
production until it passes:

- point-in-time data enforcement;
- survivorship-bias review;
- realistic transaction-cost and slippage stress;
- walk-forward selection with untouched out-of-sample evaluation;
- parameter-stability and regime analysis;
- explicit instrument applicability for common stocks, ETFs, and ADRs;
- independent review of data availability and commercial-use rights.

Historical predictive evidence is not a promise of future returns and does not
turn a screening result into investment advice.
