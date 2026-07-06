# Q649: Signedness or zero-crossing bug in accounting math

## Question
Can attacker-controlled sign changes around core/contracts/ClearinghouseLiq.sol / _assertCanLiquidateLiability(IEndpoint.LiquidateSubaccount calldata txn, ISpotEngine spotEngine, IPerpEngine perpEngine) cause a zero-crossing, absolute-value, or multiplication path to switch accounting regimes incorrectly and grant a balance, rebate, or risk weight the user should not have?

## Target
- File/function: core/contracts/ClearinghouseLiq.sol / _assertCanLiquidateLiability(IEndpoint.LiquidateSubaccount calldata txn, ISpotEngine spotEngine, IPerpEngine perpEngine)
- Entrypoint: User manipulates account state through trading, settlement, or transfer flows before triggering liquidation or finalization.
- Attacker controls: liquidator subaccount, liquidatee subaccount, productId, isEncodedSpread, amount, nonce, quote balance state, spread composition
- Exploit idea: Force transitions across positive, zero, and negative boundaries and compare the post-state to a reference implementation that models the intended sign semantics explicitly.
- Invariant to test: Delegatecalled liquidation logic must remain storage-safe and synchronized with clearinghouse accounting.
- Expected HackenProof impact: Critical/High: overflow/underflow or logic attack that breaks accounting and can be monetized.
- Fast validation: Write a Hardhat scenario that sets up healthy and unhealthy accounts, then fuzz liquidation amounts, spread encodings, and settlement ordering to assert exact seize bounds.
