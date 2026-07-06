# Q3101: Same-block or same-transaction multi-call interference

## Question
Can two attacker-controlled calls that both reach core/contracts/Clearinghouse.sol / forceRebalanceNlpPool(IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18) within the same block or bundled transaction interfere with each other so that the second call observes partially updated state, stale checks, or unexpectedly shared replay/accounting state?

## Target
- File/function: core/contracts/Clearinghouse.sol / forceRebalanceNlpPool(IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18)
- Entrypoint: User submits a signed withdrawal, transfer, liquidation, or settlement action that EndpointTx routes into Clearinghouse.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Bundle duplicate or adjacent calls into the same block or relayed sequence, then compare the result to isolated execution to see whether core/contracts/Clearinghouse.sol / forceRebalanceNlpPool(IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18) leaks value or authorization between the calls.
- Invariant to test: Back-to-back reachable calls must not share intermediate state in a way that enables replay, double-credit, wrong-recipient routing, or stale health assumptions.
- Expected HackenProof impact: Critical/High: transaction manipulation, replay, or logic attack through same-block interference.
- Fast validation: Fuzz product IDs, decimals, health states, and sendTo values around Clearinghouse entrypoints and assert post-state solvency and ownership invariants.
