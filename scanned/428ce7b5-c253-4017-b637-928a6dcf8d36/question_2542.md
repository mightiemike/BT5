# Q2542: Stale or double-applied isolatedSubaccountsMask

## Question
Can attacker-controlled sequencing make core/contracts/OffchainExchange.sol / dumpFees(...) consume stale isolatedSubaccountsMask or apply the same isolatedSubaccountsMask transition twice, causing unauthorized settlement, replayed withdrawal, or incorrect margin accounting?

## Target
- File/function: core/contracts/OffchainExchange.sol / dumpFees(...)
- Entrypoint: User later closes, claims, or settles through order-driven exchange flows that mutate OffchainExchange state.
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Attempt back-to-back calls, delayed queue execution, repeated fills, or same-block sequences that reuse stale isolatedSubaccountsMask before all related state is finalized.
- Invariant to test: Filled amount tracking, isolated-subaccount routing, fee accounting, and quote/base deltas must remain conserved across every fill and close path.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through bad fill accounting, builder-fee routing, or isolated margin handling.
- Fast validation: Fuzz order digest inputs, filledAmounts tracking, and builder fee fields, then assert the same economic order cannot settle twice or on a different market.
