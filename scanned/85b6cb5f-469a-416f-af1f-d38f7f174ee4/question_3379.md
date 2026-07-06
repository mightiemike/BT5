# Q3379: Ordering dependency around withdrawal idx

## Question
Can an attacker manipulate reachable call order so that core/contracts/Clearinghouse.sol / mintNlp(IEndpoint.MintNlp calldata txn, int128 oraclePriceX18, IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18) observes withdrawal idx in the wrong sequence and therefore settles, withdraws, liquidates, or credits value under assumptions that were only valid before reordering?

## Target
- File/function: core/contracts/Clearinghouse.sol / mintNlp(IEndpoint.MintNlp calldata txn, int128 oraclePriceX18, IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18)
- Entrypoint: User submits a signed NLP, transferQuote, or settlePnl flow that eventually mutates clearinghouse state.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Reorder the same user actions around withdrawal idx, including queue execution, order matching, funding updates, settlement loops, and withdrawal idx progression, then compare final balances.
- Invariant to test: Clearinghouse health, insurance, withdrawal, and settlement accounting must remain solvent and synchronized across engines and pools.
- Expected HackenProof impact: Critical/High: reordering or transaction manipulation causing invalid execution or fund loss.
- Fast validation: Fuzz product IDs, decimals, health states, and sendTo values around Clearinghouse entrypoints and assert post-state solvency and ownership invariants.
