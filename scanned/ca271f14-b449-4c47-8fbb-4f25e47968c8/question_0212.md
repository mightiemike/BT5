# Q212: First-use, zero-state, or empty-state boundary bug

## Question
Can the first interaction with a fresh nonce, empty balance, empty mapping slot, uninitialized queue entry, first fill, first claim, or first isolated-subaccount state around core/contracts/BaseEngine.sol / _addOrUpdateProduct(uint32 productId, uint32 quoteId, int128 sizeIncrement, int128 minSize, RiskHelper.RiskStore memory riskStore) behave differently enough from later interactions to create an exploitable accounting or authorization gap?

## Target
- File/function: core/contracts/BaseEngine.sol / _addOrUpdateProduct(uint32 productId, uint32 quoteId, int128 sizeIncrement, int128 minSize, RiskHelper.RiskStore memory riskStore)
- Entrypoint: User reaches BaseEngine bookkeeping indirectly through any deposit, withdraw, trade, liquidation, or settlement action.
- Attacker controls: productId, subaccount, risk weights, nonZeroBalances bitmap state, amount and quote changes
- Exploit idea: Compare the exact first-use path against the steady-state path for core/contracts/BaseEngine.sol / _addOrUpdateProduct(uint32 productId, uint32 quoteId, int128 sizeIncrement, int128 minSize, RiskHelper.RiskStore memory riskStore), especially around zero balances, empty mappings, untouched fee state, empty arrays, and first-time sender or subaccount initialization.
- Invariant to test: Bitmap iteration, health contribution, and risk-weight application must not skip positions, misprice risk, or let attacker-controlled state hide liabilities.
- Expected HackenProof impact: Critical/High: logic attack or unauthorized transaction through inconsistent zero-state handling.
- Fast validation: Build a model test that mutates sparse and dense product bitmaps and asserts BaseEngine health contribution matches explicit per-product summation.
