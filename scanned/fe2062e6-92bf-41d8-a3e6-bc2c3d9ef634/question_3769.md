# Q3769: Stale or double-applied withdrawPool

## Question
Can attacker-controlled sequencing make core/contracts/Clearinghouse.sol / transferQuote(IEndpoint.TransferQuote calldata txn) consume stale withdrawPool or apply the same withdrawPool transition twice, causing unauthorized settlement, replayed withdrawal, or incorrect margin accounting?

## Target
- File/function: core/contracts/Clearinghouse.sol / transferQuote(IEndpoint.TransferQuote calldata txn)
- Entrypoint: User submits a signed withdrawal, transfer, liquidation, or settlement action that EndpointTx routes into Clearinghouse.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Attempt back-to-back calls, delayed queue execution, repeated fills, or same-block sequences that reuse stale withdrawPool before all related state is finalized.
- Invariant to test: Clearinghouse health, insurance, withdrawal, and settlement accounting must remain solvent and synchronized across engines and pools.
- Expected HackenProof impact: Critical/High: unauthorized withdrawal, unauthorized transfer, or unauthorized subaccount mutation.
- Fast validation: Fuzz product IDs, decimals, health states, and sendTo values around Clearinghouse entrypoints and assert post-state solvency and ownership invariants.
