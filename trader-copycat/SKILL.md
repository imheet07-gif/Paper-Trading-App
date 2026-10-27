---
name: trader-copycat
description: >-
  The Reverse Engineer, India edition. Finds and follows named investors in
  India's public filings: every listed company's quarterly shareholding
  pattern names its promoters and every public holder above 1% (the
  "superstar investor" portfolios), and NSE's bulk and block deals name big
  buyers and sellers every day. Discovers who holds the most, follows the
  investors the user picks (joining spellings only when the user confirms
  them), shows what each bought, added, trimmed or left since the previous
  quarter, and writes their holdings as target weights for trader-budget.
  Use when the user says "who are the big investors", "what does X own",
  "copy X's portfolio", "follow this investor", "what did they buy last
  quarter", "superstar portfolios", or names an Indian investor or fund.
argument-hint: "[investor name]"
---

# trader-copycat

India has no 13F. What it has is better in one way and worse in another:
every listed company files its shareholding pattern each quarter, naming its
promoters and every public holder of more than 1%. So a big investor's
portfolio can be rebuilt from the filings of the companies they hold, but
only the holdings above 1% of a company are visible.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/copycat.py" discover                       # biggest named holders, latest quarter
python3 "${CLAUDE_SKILL_DIR}/copycat.py" discover --who individuals --top 40
python3 "${CLAUDE_SKILL_DIR}/copycat.py" discover --by count            # most companies, not most rupees
python3 "${CLAUDE_SKILL_DIR}/copycat.py" search kacholia                # a name and all its spellings
python3 "${CLAUDE_SKILL_DIR}/copycat.py" follow add "ASHISH KACHOLIA" --as "Ashish Kacholia"
python3 "${CLAUDE_SKILL_DIR}/copycat.py" follow alias "Ashish Kacholia" "ASHISH RAMESHCHANDRA KACHOLIA"
python3 "${CLAUDE_SKILL_DIR}/copycat.py" show "Ashish Kacholia"
python3 "${CLAUDE_SKILL_DIR}/copycat.py" copy "Ashish Kacholia" --top 10 --write
```

## Where the data comes from

All from the user's own market store, built on their machine:

| table | built by | what |
| --- | --- | --- |
| `shareholding_holders` | `core/shareholding.py fetch --history-all --xbrl-since YYYY-MM-DD` | each filing's named holders: promoters and public holders above 1%, their group (mutual fund, insurer, resident individual above Rs 2 lakh, ...), percent and shares, keyed on the filing's broadcast time |
| `deals` | the scan's evening update (`core/deals.py`) | bulk and block deals with the client's name |
| `prices` | the scan's evening update | the quarter-end close, to value each holding |

A first fill of two quarters for every company is a long run (hours, best
overnight); after that, each new quarter is an incremental run.

## Steps

1. `discover` to see who is there. By default it lists public holders by
   rupee value; `--who individuals` shows people only, `--who institutions`
   funds and insurers, `--promoters` adds promoter groups, `--by count`
   ranks by number of companies.
2. `search` a name to see every spelling the filings use. NSE masks PANs,
   so names are the only link between filings, and the same person can be
   spelled several ways.
3. `follow add` the investor the user picks, then `follow alias` each other
   spelling the user confirms is the same person. Never join two spellings
   on your own: two people can share a name.
4. `show` the followed investor: each company, what changed since that
   company's previous filed quarter, the percent before and now, the value,
   and the date the filing became public; then their bulk and block deals
   since the quarter end. Say the date: these holdings are weeks old.
5. If the user wants to copy: `copy --top N --write` writes
   `trader/targets.json` in proportion to each holding's value, then
   `/trader-budget` sizes it, `/trader-risk` checks it, and `/trader-broker`
   paper-trades each line the user says yes to.

## What a filing can and cannot say

- **Only above 1%.** A public holder below 1% of a company is not named.
  When a holder disappears from a company's filing, they sold, or they fell
  below 1%; the report says "gone: sold, or now below 1%", never "sold".
- **Weeks late.** Holdings are as of the quarter end and become public when
  the company files, often three weeks later. Each row shows that date.
- **Revisions.** A revised filing replaces the original; the latest filing
  for a company's quarter is the one used.
- **Names.** Spellings are grouped by a normalised key (case, punctuation,
  Mr/Shri, Ltd/Limited). Different spellings beyond that stay separate until
  the user joins them with `follow alias`.

## Privacy

The filings name private individuals. Names stay in the user's local store
and `trader/copycat.json`; neither is ever committed. Do not paste lists of
individuals' holdings anywhere outside the user's own conversation.

## What it never does

- Never calls a holding a recommendation or says the investor is right.
- Never joins two names without the user's word, never fills in a missing
  holding, and never says "sold" when the data says "not named".
- Never places an order. A copy ends as target weights.

## Safety

- Education and tooling, not financial advice. Copying a public portfolio copies an old snapshot of
  someone else's decisions, without their reasons or their exits.
- Nothing in this project places, modifies or cancels an order: weights go to trader-budget, lines to
  trader-risk, and only lines the user says yes to are paper-traded by trader-broker.
- Paper trading by default. Going live is the user's own decision, entered by hand in their own broker.
