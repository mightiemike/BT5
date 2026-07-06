# Q2360: Spread or isolated-account liquidation mismatch

## Question
Can core/contracts/ClearinghouseLiq.sol / liquidateSubaccountImpl(IEndpoint.LiquidateSubaccount calldata txn) mis-handle spread encoding, isolated-subaccount closure, or quote availability checks so that a user is liquidated on the wrong exposure or an insolvent account escapes the intended liquidation order?

## Target
- File/function: core/contracts/ClearinghouseLiq.sol / liquidateSubaccountImpl(IEndpoint.LiquidateSubaccount calldata txn)
- Entrypoint: User submits a signed liquidation transaction that EndpointTx routes into Clearinghouse.liquidateSubaccount(...), which delegatecalls ClearinghouseLiq.
- Attacker controls: liquidator subaccount, liquidatee subaccount, productId, isEncodedSpread, amount, nonce, quote balance state, spread composition
- Exploit idea: Fuzz isEncodedSpread, productId encoding, isolated-subaccount state, and quote balances while tracking whether spread legs and parent-child state stay synchronized.
- Invariant to test: Liquidation ordering across spreads, liabilities, and PnL settlement must not let a user escape bad debt or overcharge another account.
- Expected HackenProof impact: Critical/High: logic attack creating bad debt or draining insurance through liquidation math or ordering.
- Fast validation: Fuzz quote balances, spread products, and product iteration order to test whether liquidation leaves insurance, balances, and open interest conserved.
