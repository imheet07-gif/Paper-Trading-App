# Concepts

> Shared domain vocabulary for this project: entities, named processes, and status concepts with project-specific meaning. Seeded with core domain vocabulary, then accretes as ce-compound and ce-compound-refresh process learnings; direct edits are fine. Glossary only, not a spec or catch-all.

## Market data

### Security
One listed company share followed through its whole trading life, identified by its latest ISIN even when its history spans older ISINs.
*Avoid:* stock (when the distinction from a single ISIN matters), scrip

A security's price history, corporate actions and research results are always taken over its ISIN chain, never over one ISIN alone.

### ISIN chain
The ordered list of ISINs that one security has traded under, oldest first. A new ISIN joins the chain when the old one stops trading and the new one starts under the same exchange symbol within a few days, as happens on most face value changes; a symbol reused by a different company after a long gap does not join.

### Bhavcopy
The exchange's end-of-day file of every security's open, high, low, close, volume and traded value for one trading day. It records trades exactly as they happened and is never adjusted for corporate actions, including its previous-close column.

### Delivery percentage
The share of a day's traded quantity that settled as an actual transfer of shares rather than being squared off within the day; published by the exchange per security per day.

## Corporate actions

### Corporate action
A company event that changes the number or value of shares outstanding without trading, announced with an ex-date: a bonus, a face value split or consolidation, a rights issue, a demerger or other restructure, or a dividend.

### Ex-date
The first trading day on which a buyer no longer receives the corporate action's benefit, and so the day the share price reflects it.

### Adjustment factor
The multiplier applied to every price before an ex-date so that returns across the event are real returns. All of one security's events on one ex-date act together, so their factors multiply. Dividends are recorded but not applied: research prices are price-only, not total return.

A restructure whose announcement states no ratio takes its factor from the ex-date price move and is marked price-implied; that factor is dropped when the same day also has a stated factor, so a move is never adjusted twice.

### Factor check
The test of a security-day's combined adjustment factor against what the price actually did on the ex-date: after adjustment the move must look like a normal trading day. A failed factor check means the announcement and the prices disagree.

### Data issue window
The period after a failed factor check during which the security is not eligible for research or signals, because its adjusted prices cannot be trusted there.

## Flagged ambiguities

- "Previous close" in the bhavcopy is the prior day's actual close, not an adjusted price: it does not change on ex-dates.
