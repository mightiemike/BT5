# Q194: Cross-engine conservation break

## Question
Can a reachable path through core/contracts/ClearinghouseLiq.sol / _assertCanLiquidateLiability(IEndpoint.LiquidateSubaccount calldata txn, ISpotEngine spotEngine, IPerpEngine perpEngine) change spot balances, perp balances, insurance, collected fees, or availableSettle in a way that makes the combined system value drift after a complete trade, withdrawal, settlement, or liquidation cycle?

## Target
- File/function: core/contracts/ClearinghouseLiq.sol / _assertCanLiquidateLiability(IEndpoint.LiquidateSubaccount calldata txn, ISpotEngine spotEngine, IPerpEngine perpEngine)
- Entrypoint: User manipulates account state through trading, settlement, or transfer flows before triggering liquidation or finalization.
- Attacker controls: liquidator subaccount, liquidatee subaccount, productId, isEncodedSpread, amount, nonce, quote balance state, spread composition
- Exploit idea: Model the full before/after state across spot, perp, clearinghouse, withdraw pool, builder-fee, and insurance accounting around core/contracts/ClearinghouseLiq.sol / _assertCanLiquidateLiability(IEndpoint.LiquidateSubaccount calldata txn, ISpotEngine spotEngine, IPerpEngine perpEngine); then look for any delta that is not explained by an explicit fee or transfer.
- Invariant to test: Combined spot, perp, and clearinghouse accounting must conserve value except for explicit fees and real token movements.
- Expected HackenProof impact: Critical/High: logic attack causing bad debt, insolvency, or hidden value leakage across engines.
- Fast validation: Write a Hardhat scenario that sets up healthy and unhealthy accounts, then fuzz liquidation amounts, spread encodings, and settlement ordering to assert exact seize bounds.
