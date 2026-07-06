# Q1021: Partial batch progress without full rollback

## Question
Can a loop, queue, or multi-step batch around core/contracts/ClearinghouseLiq.sol / _assertLiquidationAmount(IEndpoint.LiquidateSubaccount calldata txn, ISpotEngine spotEngine, IPerpEngine perpEngine) make economic progress on early items even though a later item fails, leaving fill state, claim state, fees, or balances inconsistent with an all-or-nothing user assumption?

## Target
- File/function: core/contracts/ClearinghouseLiq.sol / _assertLiquidationAmount(IEndpoint.LiquidateSubaccount calldata txn, ISpotEngine spotEngine, IPerpEngine perpEngine)
- Entrypoint: User manipulates account state through trading, settlement, or transfer flows before triggering liquidation or finalization.
- Attacker controls: liquidator subaccount, liquidatee subaccount, productId, isEncodedSpread, amount, nonce, quote balance state, spread composition
- Exploit idea: Construct a mixed-validity batch or queue sequence through core/contracts/ClearinghouseLiq.sol / _assertLiquidationAmount(IEndpoint.LiquidateSubaccount calldata txn, ISpotEngine spotEngine, IPerpEngine perpEngine), force one later element to fail, and compare whether earlier state changes remain committed in a way that can be exploited or replayed.
- Invariant to test: Batched or queued user actions must either preserve consistent partial-progress rules or prevent attackers from extracting value from early-commit and late-fail combinations.
- Expected HackenProof impact: Critical/High: transaction manipulation, replay, or logic attack through inconsistent partial progress handling.
- Fast validation: Write a Hardhat scenario that sets up healthy and unhealthy accounts, then fuzz liquidation amounts, spread encodings, and settlement ordering to assert exact seize bounds.
