# Q110: Cross-contract desync of insurance

## Question
Can a normal user drive core/contracts/ClearinghouseLiq.sol / _assertCanLiquidateLiability(IEndpoint.LiquidateSubaccount calldata txn, ISpotEngine spotEngine, IPerpEngine perpEngine) so that insurance is updated in one contract or storage area but not the corresponding state in another contract, leaving Nado with a reachable balance, position, or authorization desynchronization?

## Target
- File/function: core/contracts/ClearinghouseLiq.sol / _assertCanLiquidateLiability(IEndpoint.LiquidateSubaccount calldata txn, ISpotEngine spotEngine, IPerpEngine perpEngine)
- Entrypoint: User submits a signed liquidation transaction that EndpointTx routes into Clearinghouse.liquidateSubaccount(...), which delegatecalls ClearinghouseLiq.
- Attacker controls: liquidator subaccount, liquidatee subaccount, productId, isEncodedSpread, amount, nonce, quote balance state, spread composition
- Exploit idea: Target the exact moment when core/contracts/ClearinghouseLiq.sol / _assertCanLiquidateLiability(IEndpoint.LiquidateSubaccount calldata txn, ISpotEngine spotEngine, IPerpEngine perpEngine) mutates insurance and compare post-state across Endpoint, Clearinghouse, engines, pools, and exchange storage after failure, replay, or partial execution.
- Invariant to test: Delegatecalled liquidation logic must remain storage-safe and synchronized with clearinghouse accounting.
- Expected HackenProof impact: Critical/High: transaction manipulation causing liquidation at the wrong price, amount, or liability ordering.
- Fast validation: Trace delegatecall storage writes in liquidation and assert no path mutates unrelated storage slots or skips required post-checks.
