### Title
Pool Admin Can Frontrun Swap Fees to Maximum Cap Without Timelock, Causing Direct Loss to Swappers - (`metric-core/contracts/MetricOmmPoolFactory.sol`)

---

### Summary

`MetricOmmPoolFactory.setPoolAdminFees` allows any pool admin to raise the admin spread fee to 20% (`maxAdminSpreadFeeE6 = 200_000`) and the notional fee to 1% (`maxAdminNotionalFeeE8 = 1_000_000`) with immediate, same-block effect and no timelock. Because `createPool` is permissionless, any address can become a pool admin. A malicious pool admin can frontrun a large pending swap by raising fees to the cap, then reset them afterward, extracting up to 20%+ of the swapper's trade value.

---

### Finding Description

`createPool` is an unrestricted external function — any caller can deploy a pool and assign themselves as `poolAdmin`. [1](#0-0) 

The pool admin can call `setPoolAdminFees` at any time with no timelock or delay: [2](#0-1) 

The hard caps are 20% spread and 1% notional: [3](#0-2) 

The fee update is applied immediately to the pool via `setPoolFees`: [4](#0-3) 

Additionally, `setPoolBinAdditionalFees` has no cap check beyond the `uint16` type limit (~6.55% in E6) and also takes effect immediately: [5](#0-4) 

Contrast this with the price provider rotation, which correctly enforces a timelock: [6](#0-5) 

No equivalent timelock exists for fee changes.

---

### Impact Explanation

A malicious pool admin can:
1. Observe a large pending swap in the mempool.
2. Frontrun it with `setPoolAdminFees(pool, 200_000, 1_000_000)` — raising spread fee to 20% and notional fee to 1%.
3. The victim's swap executes at the elevated fee rate, losing up to ~21% of trade value.
4. The admin calls `collectPoolFees` to extract the inflated admin fee share.
5. The admin resets fees to normal.

The `collectFees` call inside `setPoolAdminFees` first sweeps accrued fees at the old rate before applying the new rate, so the admin cleanly separates the pre-attack and attack-period fee accrual. [7](#0-6) 

This is a direct loss of user principal (swap output tokens) with no recourse.

---

### Likelihood Explanation

- `createPool` is permissionless — any address can become a pool admin.
- No UI warning mechanism is enforced on-chain.
- The attack requires only two transactions (fee raise + reset) and is profitable on any swap large enough to cover gas.
- The `PriceVelocityGuardExtension` documentation explicitly acknowledges that "the pool admin must be trusted," confirming the protocol recognizes this trust assumption but provides no on-chain enforcement for fee changes. [8](#0-7) 

---

### Recommendation

Add a timelock to `setPoolAdminFees` and `setPoolBinAdditionalFees` analogous to the existing `priceProviderTimelock` mechanism. A propose-then-execute pattern (with a minimum delay, e.g., 24–48 hours) would allow users to observe pending fee changes and exit before they take effect, eliminating the frontrunning vector.

---

### Proof of Concept

```
1. Attacker calls createPool(..., admin = attacker, adminSpreadFeeE6 = 0, ...)
   → attacker is poolAdmin; pool starts with 0% admin fee.

2. Attacker advertises pool; users begin swapping.

3. Victim submits a large swap tx (e.g., 100,000 USDC → token1).

4. Attacker sees victim's tx in mempool; frontruns with:
   factory.setPoolAdminFees(pool, 200_000, 1_000_000)
   → admin spread fee = 20%, notional fee = 1%; takes effect immediately.

5. Victim's swap executes: ~21% of 100,000 USDC = ~21,000 USDC lost to fees.

6. Attacker calls factory.collectPoolFees(pool)
   → admin fee share (~21,000 USDC) transferred to attacker's fee destination.

7. Attacker calls factory.setPoolAdminFees(pool, 0, 0) to reset.
```

The `setPoolFees` call inside step 4 updates the pool's live `spreadFeeE6` and `notionalFeeE8` in the same block, before the victim's swap is included. [9](#0-8)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L44-45)
```text
  uint24 internal constant HARD_MAX_SPREAD_FEE_E6 = 200_000;
  uint24 internal constant HARD_MAX_NOTIONAL_FEE_E8 = 1_000_000;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L156-156)
```text
  function createPool(PoolParameters calldata params) external override returns (address pool) {
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L408-435)
```text
  function setPoolAdminFees(address pool, uint24 newAdminSpreadFeeE6, uint24 newAdminNotionalFeeE8)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();

    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );

    c.adminSpreadFeeE6 = newAdminSpreadFeeE6;
    c.adminNotionalFeeE8 = newAdminNotionalFeeE8;
    poolFeeConfig[pool] = c;

    IMetricOmmPoolFactoryActions(pool)
      .setPoolFees(c.protocolSpreadFeeE6 + c.adminSpreadFeeE6, c.protocolNotionalFeeE8 + c.adminNotionalFeeE8);
    emit PoolAdminSpreadFeeUpdated(pool, newAdminSpreadFeeE6);
    emit PoolAdminNotionalFeeUpdated(pool, newAdminNotionalFeeE8);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L450-457)
```text
  function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L487-490)
```text
    uint256 executeAfter = block.timestamp + timelock;
    pendingPriceProvider[pool] = newPriceProvider;
    pendingPriceProviderExecuteAfter[pool] = executeAfter;
    emit PoolPriceProviderChangeProposed(pool, current, newPriceProvider, executeAfter);
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L11-13)
```text
/// @dev This extension allows the pool admin to increase security of the pool by limiting price
///      manipulation through velocity constraints. However, it assumes that the pool admin is not
///      an adversary and acts to optimize pool profitability. The pool admin must be trusted.
```

**File:** metric-core/contracts/MetricOmmPool.sol (L437-452)
```text
  function setPoolFees(uint24 newSpreadFeeE6, uint24 newNotionalFeeE8)
    external
    onlyFactory
    nonReentrant(PoolActions.SET_POOL_FEES)
  {
    unchecked {
      if (newSpreadFeeE6 != spreadFeeE6) {
        spreadFeeE6 = newSpreadFeeE6;
        emit SpreadFeeUpdated(newSpreadFeeE6);
      }
      if (newNotionalFeeE8 != notionalFeeE8) {
        notionalFeeE8 = newNotionalFeeE8;
        emit NotionalFeeUpdated(newNotionalFeeE8);
      }
    }
  }
```
