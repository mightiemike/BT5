# Q3273: Over-liquidation or under-collateralized finalization

## Question
Can a user manipulate account state before reaching core/contracts/Clearinghouse.sol / liquidateSubaccount(IEndpoint.LiquidateSubaccount calldata txn) so that liquidation math or ordering lets the liquidator seize too much, settle PnL in the wrong order, or finalize with bad debt still hidden?

## Target
- File/function: core/contracts/Clearinghouse.sol / liquidateSubaccount(IEndpoint.LiquidateSubaccount calldata txn)
- Entrypoint: User submits a signed withdrawal, transfer, liquidation, or settlement action that EndpointTx routes into Clearinghouse.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Compose trades, spreads, funding state, quote balances, and liquidation amount choices that stress the exact branching in core/contracts/Clearinghouse.sol / liquidateSubaccount(IEndpoint.LiquidateSubaccount calldata txn) before and after positive/negative PnL settlement.
- Invariant to test: Liquidation must only reduce risk by an allowed amount and must not extract more value than permitted or hide residual bad debt.
- Expected HackenProof impact: Critical/High: stealing or loss of funds from the exchange, withdraw pool, or insurance accounting.
- Fast validation: Write a Hardhat invariant that tracks ERC20 balances, withdraw-pool balances, insurance, and engine balances through deposit/withdraw/settle/liquidate sequences.
