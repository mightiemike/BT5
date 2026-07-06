# Q877: Failure-handling mismatch after IOffchainExchange.tryCloseIsolatedSubaccount(...)

## Question
Can attacker-controlled failure behavior around IOffchainExchange.tryCloseIsolatedSubaccount(...) leave core/contracts/ClearinghouseLiq.sol / _assertLiquidationAmount(IEndpoint.LiquidateSubaccount calldata txn, ISpotEngine spotEngine, IPerpEngine perpEngine) in a partially-applied state where assets moved, but balances, fees, or replay markers did not settle consistently?

## Target
- File/function: core/contracts/ClearinghouseLiq.sol / _assertLiquidationAmount(IEndpoint.LiquidateSubaccount calldata txn, ISpotEngine spotEngine, IPerpEngine perpEngine)
- Entrypoint: User submits a signed liquidation transaction that EndpointTx routes into Clearinghouse.liquidateSubaccount(...), which delegatecalls ClearinghouseLiq.
- Attacker controls: liquidator subaccount, liquidatee subaccount, productId, isEncodedSpread, amount, nonce, quote balance state, spread composition
- Exploit idea: Force IOffchainExchange.tryCloseIsolatedSubaccount(...) to revert, return false, consume abnormal gas, or partially succeed and compare protocol state before and after the revert path.
- Invariant to test: Delegatecalled liquidation logic must remain storage-safe and synchronized with clearinghouse accounting.
- Expected HackenProof impact: Critical/High: unauthorized liquidation or over-liquidation of a healthy user account.
- Fast validation: Write a Hardhat scenario that sets up healthy and unhealthy accounts, then fuzz liquidation amounts, spread encodings, and settlement ordering to assert exact seize bounds.
