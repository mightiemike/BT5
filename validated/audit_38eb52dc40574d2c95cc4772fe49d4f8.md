I need to systematically trace the BlueBerry bug class — **position consumed in full while the amount returned to the user is capped at a stale/original value, permanently locking accrued value** — through the Metric OMM extension guards and hook plumbing.

**Step 1 — Classify the external invariant**

The BlueBerry invariant: `shares_burned ≠ tokens_returned` because a cap (`pos.underlyingAmount`) is applied to the returned amount while the full `shareAmount` is deducted from the position. Accrued interest is permanently locked.

**Step 2 — Map to Metric OMM candidates**

The closest analogs are:
- `LiquidityLib.removeLiquidity` (shares burned → tokens returned)
- `MetricOmmPool.collectFees` (fee pool consumed → tokens transferred)
- Extension hooks (`OracleValueStopLossExtension`, `PriceVelocityGuardExtension`, `DepositAllowlistExtension`, `SwapAllowlistExtension`)

**Step 3 — Trace `removeLiquidity`**

`LiquidityLib.removeLiquidity` computes:
```
amount0Scaled = binState.token