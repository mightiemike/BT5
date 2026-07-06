# Q876: Callback-driven post-state ordering bug

## Question
Can a token, recipient, or helper callback interacting around core/contracts/Clearinghouse.sol / burnNlp(IEndpoint.BurnNlp calldata txn, int128 oraclePriceX18, IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18) observe a half-updated state and force a second reachable action before all balances, replay markers, fee state, or claim state are finalized?

## Target
- File/function: core/contracts/Clearinghouse.sol / burnNlp(IEndpoint.BurnNlp calldata txn, int128 oraclePriceX18, IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18)
- Entrypoint: User deposits collateral through Endpoint and the call lands in Clearinghouse.depositCollateral(...).
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Use malicious token hooks, recipient fallback logic, helper contracts, or chained calls around core/contracts/Clearinghouse.sol / burnNlp(IEndpoint.BurnNlp calldata txn, int128 oraclePriceX18, IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18); then verify whether any second action can read or exploit intermediate state before finalization.
- Invariant to test: A user must not withdraw, transfer, mint, burn, or settle against collateral or equity they do not actually own.
- Expected HackenProof impact: Critical/High: reentrancy or transaction manipulation through externally observable intermediate state.
- Fast validation: Fuzz product IDs, decimals, health states, and sendTo values around Clearinghouse entrypoints and assert post-state solvency and ownership invariants.
