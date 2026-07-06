# Q3664: Stale or double-applied insurance

## Question
Can attacker-controlled sequencing make core/contracts/Clearinghouse.sol / registerProduct(uint32 productId) consume stale insurance or apply the same insurance transition twice, causing unauthorized settlement, replayed withdrawal, or incorrect margin accounting?

## Target
- File/function: core/contracts/Clearinghouse.sol / registerProduct(uint32 productId)
- Entrypoint: User submits a signed NLP, transferQuote, or settlePnl flow that eventually mutates clearinghouse state.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Attempt back-to-back calls, delayed queue execution, repeated fills, or same-block sequences that reuse stale insurance before all related state is finalized.
- Invariant to test: Clearinghouse health, insurance, withdrawal, and settlement accounting must remain solvent and synchronized across engines and pools.
- Expected HackenProof impact: Critical/High: unauthorized withdrawal, unauthorized transfer, or unauthorized subaccount mutation.
- Fast validation: Fuzz product IDs, decimals, health states, and sendTo values around Clearinghouse entrypoints and assert post-state solvency and ownership invariants.
