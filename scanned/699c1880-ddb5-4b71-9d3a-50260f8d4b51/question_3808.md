# Q3808: Rounding leak through decimals multiplier

## Question
Can repeated user-controlled updates around decimals multiplier make core/contracts/Clearinghouse.sol / updateFeeTier(bytes calldata transaction) round in the attacker’s favor so that quote, collateral, fee, or PnL value leaks out of conservation over multiple reachable transactions?

## Target
- File/function: core/contracts/Clearinghouse.sol / updateFeeTier(bytes calldata transaction)
- Entrypoint: User deposits collateral through Endpoint and the call lands in Clearinghouse.depositCollateral(...).
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Search for floor, ceil, division, multiplier, and size-increment boundaries involving decimals multiplier; then repeat small-value cycles until any leaked balance becomes measurable.
- Invariant to test: A user must not withdraw, transfer, mint, burn, or settle against collateral or equity they do not actually own.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that drains value via repeated rounding leakage.
- Fast validation: Write a Hardhat invariant that tracks ERC20 balances, withdraw-pool balances, insurance, and engine balances through deposit/withdraw/settle/liquidate sequences.
