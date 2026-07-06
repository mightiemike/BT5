# Q2859: Rounding leak through amount

## Question
Can repeated user-controlled updates around amount make core/contracts/Clearinghouse.sol / depositCollateral(IEndpoint.DepositCollateral calldata txn) round in the attacker’s favor so that quote, collateral, fee, or PnL value leaks out of conservation over multiple reachable transactions?

## Target
- File/function: core/contracts/Clearinghouse.sol / depositCollateral(IEndpoint.DepositCollateral calldata txn)
- Entrypoint: User submits a signed withdrawal, transfer, liquidation, or settlement action that EndpointTx routes into Clearinghouse.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Search for floor, ceil, division, multiplier, and size-increment boundaries involving amount; then repeat small-value cycles until any leaked balance becomes measurable.
- Invariant to test: A user must not withdraw, transfer, mint, burn, or settle against collateral or equity they do not actually own.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that drains value via repeated rounding leakage.
- Fast validation: Write a Hardhat invariant that tracks ERC20 balances, withdraw-pool balances, insurance, and engine balances through deposit/withdraw/settle/liquidate sequences.
