# Q3369: Health-check bypass through stale or incomplete risk inputs

## Question
Can an attacker reach core/contracts/Clearinghouse.sol / mintNlp(IEndpoint.MintNlp calldata txn, int128 oraclePriceX18, IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18) with a portfolio shape that hides a liability, spread leg, borrowed spot, or unsettled perp loss from the health calculation used by the calling flow?

## Target
- File/function: core/contracts/Clearinghouse.sol / mintNlp(IEndpoint.MintNlp calldata txn, int128 oraclePriceX18, IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18)
- Entrypoint: User submits a signed withdrawal, transfer, liquidation, or settlement action that EndpointTx routes into Clearinghouse.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Build portfolios spanning spot, perp, spread, isolated, and NLP balances, then compare explicit risk aggregation against the health result consumed around core/contracts/Clearinghouse.sol / mintNlp(IEndpoint.MintNlp calldata txn, int128 oraclePriceX18, IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18).
- Invariant to test: Health checks must include every reachable liability and must not let a user withdraw, transfer, or avoid liquidation with non-existent equity.
- Expected HackenProof impact: Critical/High: logic attack causing bad debt, unauthorized withdrawal, or liquidation bypass.
- Fast validation: Fuzz product IDs, decimals, health states, and sendTo values around Clearinghouse entrypoints and assert post-state solvency and ownership invariants.
