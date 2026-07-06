# Q985: Ordering dependency around spread iteration order

## Question
Can an attacker manipulate reachable call order so that core/contracts/ClearinghouseLiq.sol / _assertLiquidationAmount(IEndpoint.LiquidateSubaccount calldata txn, ISpotEngine spotEngine, IPerpEngine perpEngine) observes spread iteration order in the wrong sequence and therefore settles, withdraws, liquidates, or credits value under assumptions that were only valid before reordering?

## Target
- File/function: core/contracts/ClearinghouseLiq.sol / _assertLiquidationAmount(IEndpoint.LiquidateSubaccount calldata txn, ISpotEngine spotEngine, IPerpEngine perpEngine)
- Entrypoint: User submits a signed liquidation transaction that EndpointTx routes into Clearinghouse.liquidateSubaccount(...), which delegatecalls ClearinghouseLiq.
- Attacker controls: liquidator subaccount, liquidatee subaccount, productId, isEncodedSpread, amount, nonce, quote balance state, spread composition
- Exploit idea: Reorder the same user actions around spread iteration order, including queue execution, order matching, funding updates, settlement loops, and withdrawal idx progression, then compare final balances.
- Invariant to test: Liquidation ordering across spreads, liabilities, and PnL settlement must not let a user escape bad debt or overcharge another account.
- Expected HackenProof impact: Critical/High: reordering or transaction manipulation causing invalid execution or fund loss.
- Fast validation: Fuzz quote balances, spread products, and product iteration order to test whether liquidation leaves insurance, balances, and open interest conserved.
