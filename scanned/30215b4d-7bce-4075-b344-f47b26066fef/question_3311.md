# Q3311: Spread or isolated-account liquidation mismatch

## Question
Can core/contracts/Clearinghouse.sol / liquidateSubaccount(IEndpoint.LiquidateSubaccount calldata txn) mis-handle spread encoding, isolated-subaccount closure, or quote availability checks so that a user is liquidated on the wrong exposure or an insolvent account escapes the intended liquidation order?

## Target
- File/function: core/contracts/Clearinghouse.sol / liquidateSubaccount(IEndpoint.LiquidateSubaccount calldata txn)
- Entrypoint: User submits a signed NLP, transferQuote, or settlePnl flow that eventually mutates clearinghouse state.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Fuzz isEncodedSpread, productId encoding, isolated-subaccount state, and quote balances while tracking whether spread legs and parent-child state stay synchronized.
- Invariant to test: Clearinghouse health, insurance, withdrawal, and settlement accounting must remain solvent and synchronized across engines and pools.
- Expected HackenProof impact: Critical/High: unauthorized withdrawal, unauthorized transfer, or unauthorized subaccount mutation.
- Fast validation: Fuzz product IDs, decimals, health states, and sendTo values around Clearinghouse entrypoints and assert post-state solvency and ownership invariants.
