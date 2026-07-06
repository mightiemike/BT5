# Q2303: Stale cache or memoized-state window

## Question
Can core/contracts/BaseEngine.sol / updatePrice(uint32 productId, int128 priceX18) read a cached market, health, fee, builder, funding, or balance value that becomes stale before the rest of execution finishes, so later steps act on assumptions that are no longer true?

## Target
- File/function: core/contracts/BaseEngine.sol / updatePrice(uint32 productId, int128 priceX18)
- Entrypoint: User reaches BaseEngine bookkeeping indirectly through any deposit, withdraw, trade, liquidation, or settlement action.
- Attacker controls: productId, subaccount, risk weights, nonZeroBalances bitmap state, amount and quote changes
- Exploit idea: Identify any state snapshot, cached struct, or copied market state used across multiple branches in core/contracts/BaseEngine.sol / updatePrice(uint32 productId, int128 priceX18); then mutate the underlying live state through a reachable interleaving and compare the cached result to fresh reads.
- Invariant to test: A cached or memoized view of state must not remain valid across later user-reachable transitions that can change the economic outcome.
- Expected HackenProof impact: Critical/High: reordering or logic attack through stale cached state.
- Fast validation: Build a model test that mutates sparse and dense product bitmaps and asserts BaseEngine health contribution matches explicit per-product summation.
