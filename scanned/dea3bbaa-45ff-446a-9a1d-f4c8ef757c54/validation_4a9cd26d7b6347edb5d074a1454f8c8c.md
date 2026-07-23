### Title
Missing Deadline in `MetricOmmPoolLiquidityAdder` Allows Stale-Price Liquidity Deposits — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmPoolLiquidityAdder` exposes `addLiquidityExactShares` and `addLiquidityWeighted` with no deadline parameter. `MetricOmmSimpleRouter` explicitly guards every swap with `_checkDeadline`, but the liquidity-adder path has no equivalent. A transaction that sits in the mempool and executes after the oracle price has moved forces the LP to deposit at a price they never accepted, causing direct, immediate impermanent loss bounded only by the caller-supplied token caps.

---

### Finding Description

`MetricOmmSwapRouterBase._check