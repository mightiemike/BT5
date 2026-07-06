# Q107: Cross-contract desync of nonZeroBalances

## Question
Can a normal user drive core/contracts/BaseEngine.sol / _addOrUpdateProduct(uint32 productId, uint32 quoteId, int128 sizeIncrement, int128 minSize, RiskHelper.RiskStore memory riskStore) so that nonZeroBalances is updated in one contract or storage area but not the corresponding state in another contract, leaving Nado with a reachable balance, position, or authorization desynchronization?

## Target
- File/function: core/contracts/BaseEngine.sol / _addOrUpdateProduct(uint32 productId, uint32 quoteId, int128 sizeIncrement, int128 minSize, RiskHelper.RiskStore memory riskStore)
- Entrypoint: User reaches BaseEngine bookkeeping indirectly through any deposit, withdraw, trade, liquidation, or settlement action.
- Attacker controls: productId, subaccount, risk weights, nonZeroBalances bitmap state, amount and quote changes
- Exploit idea: Target the exact moment when core/contracts/BaseEngine.sol / _addOrUpdateProduct(uint32 productId, uint32 quoteId, int128 sizeIncrement, int128 minSize, RiskHelper.RiskStore memory riskStore) mutates nonZeroBalances and compare post-state across Endpoint, Clearinghouse, engines, pools, and exchange storage after failure, replay, or partial execution.
- Invariant to test: Bitmap iteration, health contribution, and risk-weight application must not skip positions, misprice risk, or let attacker-controlled state hide liabilities.
- Expected HackenProof impact: Critical/High: logic attack or arithmetic bug causing bad debt, incorrect health checks, or unauthorized balance changes through stale bookkeeping.
- Fast validation: Build a model test that mutates sparse and dense product bitmaps and asserts BaseEngine health contribution matches explicit per-product summation.
