# Q3065: Liability saturation or sign-flip saturation gap

## Question
Can attacker-controlled liabilities around core/contracts/Clearinghouse.sol / forceRebalanceNlpPool(IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18) hit a max, min, abs, or sign-flip boundary where debt stops growing correctly, collateral stops shrinking correctly, or a penalty saturates before the real exposure does?

## Target
- File/function: core/contracts/Clearinghouse.sol / forceRebalanceNlpPool(IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18)
- Entrypoint: User deposits collateral through Endpoint and the call lands in Clearinghouse.depositCollateral(...).
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Push liabilities, borrows, negative PnL, spread exposures, and liquidation amounts toward every numeric boundary used around core/contracts/Clearinghouse.sol / forceRebalanceNlpPool(IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18); then compare the realized exposure to the mathematically expected exposure.
- Invariant to test: Debt, liability, and penalty accounting must remain monotonic and must not saturate early in a way that benefits the attacker.
- Expected HackenProof impact: Critical/High: overflow/underflow or logic attack causing hidden liabilities or under-penalized bad debt.
- Fast validation: Fuzz product IDs, decimals, health states, and sendTo values around Clearinghouse entrypoints and assert post-state solvency and ownership invariants.
