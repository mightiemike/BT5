# Q865: Dust-cycle extraction or min-threshold bypass

## Question
Can repeated tiny user-controlled operations through core/contracts/ClearinghouseLiq.sol / _assertLiquidationAmount(IEndpoint.LiquidateSubaccount calldata txn, ISpotEngine spotEngine, IPerpEngine perpEngine) stay below a per-step threshold, rounding guard, fee floor, or min-size rule while still accumulating a meaningful balance, position, or withdrawal advantage over many iterations?

## Target
- File/function: core/contracts/ClearinghouseLiq.sol / _assertLiquidationAmount(IEndpoint.LiquidateSubaccount calldata txn, ISpotEngine spotEngine, IPerpEngine perpEngine)
- Entrypoint: User manipulates account state through trading, settlement, or transfer flows before triggering liquidation or finalization.
- Attacker controls: liquidator subaccount, liquidatee subaccount, productId, isEncodedSpread, amount, nonce, quote balance state, spread composition
- Exploit idea: Search for floor divisions, min-size exemptions, fee-on-first-fill logic, or first-deposit thresholds around core/contracts/ClearinghouseLiq.sol / _assertLiquidationAmount(IEndpoint.LiquidateSubaccount calldata txn, ISpotEngine spotEngine, IPerpEngine perpEngine); then repeat the smallest admissible action until any measurable value leak or rule bypass appears.
- Invariant to test: Only liquidatable accounts should be liquidated, and liquidation must not seize more than allowed or manufacture insurance/funding value.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that extracts value by exploiting repeated micro-operations.
- Fast validation: Write a Hardhat scenario that sets up healthy and unhealthy accounts, then fuzz liquidation amounts, spread encodings, and settlement ordering to assert exact seize bounds.
