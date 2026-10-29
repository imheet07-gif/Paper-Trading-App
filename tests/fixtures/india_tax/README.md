# india_tax fixtures

Synthetic trades for `core/tax.py`: made-up companies (ISINs `INE000X0100x`
are not real) and round prices, chosen so the tax can be worked out by hand.
They test arithmetic on the rules in `core/tax_rates.json`, not markets.
`carry_forward.json` is the hand-worked case in `tests/test_india_tax.py`:
a short-term loss in FY2019-20 that runs out against later gains, and a
long-term loss that may only meet long-term gains.
