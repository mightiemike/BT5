# Q3642: Ordering dependency around settlePnl loop order

## Question
Can an attacker manipulate reachable call order so that core/contracts/Clearinghouse.sol / registerProduct(uint32 productId) observes settlePnl loop order in the wrong sequence and therefore settles, withdraws, liquidates, or credits value under assumptions that were only valid before reordering?

## Target
- File/function: core/contracts/Clearinghouse.sol / registerProduct(uint32 productId)
- Entrypoint: User deposits collateral through Endpoint and the call lands in Clearinghouse.depositCollateral(...).
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Reorder the same user actions around settlePnl loop order, including queue execution, order matching, funding updates, settlement loops, and withdrawal idx progression, then compare final balances.
- Invariant to test: Clearinghouse health, insurance, withdrawal, and settlement accounting must remain solvent and synchronized across engines and pools.
- Expected HackenProof impact: Critical/High: reordering or transaction manipulation causing invalid execution or fund loss.
- Fast validation: Fuzz product IDs, decimals, health states, and sendTo values around Clearinghouse entrypoints and assert post-state solvency and ownership invariants.
