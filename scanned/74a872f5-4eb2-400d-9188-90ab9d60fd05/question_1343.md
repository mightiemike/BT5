# Q1343: Stale or double-applied productToEngine

## Question
Can attacker-controlled sequencing make core/contracts/Clearinghouse.sol / burnNlp(IEndpoint.BurnNlp calldata txn, int128 oraclePriceX18, IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18) consume stale productToEngine or apply the same productToEngine transition twice, causing unauthorized settlement, replayed withdrawal, or incorrect margin accounting?

## Target
- File/function: core/contracts/Clearinghouse.sol / burnNlp(IEndpoint.BurnNlp calldata txn, int128 oraclePriceX18, IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18)
- Entrypoint: User deposits collateral through Endpoint and the call lands in Clearinghouse.depositCollateral(...).
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Attempt back-to-back calls, delayed queue execution, repeated fills, or same-block sequences that reuse stale productToEngine before all related state is finalized.
- Invariant to test: Clearinghouse health, insurance, withdrawal, and settlement accounting must remain solvent and synchronized across engines and pools.
- Expected HackenProof impact: Critical/High: unauthorized withdrawal, unauthorized transfer, or unauthorized subaccount mutation.
- Fast validation: Fuzz product IDs, decimals, health states, and sendTo values around Clearinghouse entrypoints and assert post-state solvency and ownership invariants.
