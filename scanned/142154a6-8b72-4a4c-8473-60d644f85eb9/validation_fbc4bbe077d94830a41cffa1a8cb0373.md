Looking at the `collectFees` function in `MetricOmmPool.sol` and the factory's fee management functions for the analog.

### Title
Atomic fee delivery in `collectFees` can be blocked by a blacklisted `adminFeeDestination`, locking protocol fees and bricking fee-update flows — (File: `metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool.collectFees` delivers both token0 and token1 to both the admin fee destination and the FACTORY in a single atomic call. If any one of the four `safeTransfer` calls reverts — for example because `adminFeeDestination` is USDC-blacklisted — the entire function reverts. Because `setPoolAdminFees` and `setPoolProtocolFee` in the factory unconditionally call `collectFees` before updating rates, a blocked `collectFees` also permanently blocks fee-rate changes for that pool until the pool admin intervenes.

---

### Finding Description

`MetricOmmPool.collectFees` (callable only by the FACTORY) performs four sequential token transfers inside a single `unchecked` block with no isolation between them:

```solidity
if (totalFee0ToAdmin > 0) {
    transferToken0(adminFeeDestination_, totalFee0ToAdmin);   // (1)
}
if (totalFee1ToAdmin > 0) {
    transferToken1(adminFeeDestination_, totalFee1ToAdmin);   // (2)
}
if (totalFee0ToProtocol > 0) {
    transferToken0(FACTORY, totalFee0ToProtocol);             // (3)
}
if (totalFee1ToProtocol > 0) {
    transferToken1(FACTORY, totalFee1ToProtocol);             // (4)
}
notionalFeeToken0Scaled = 0;
notionalFeeToken1Scaled = 0;
``` [1](#0-0) 

The state reset (`notionalFeeToken0Scaled = 0`) only executes after all four transfers succeed. If transfer (1) or (2) reverts (e.g., USDC blacklists `adminFeeDestination_`), the whole call reverts and no state is mutated — fees remain in the pool but cannot be distributed.

The factory's `collectPoolFees` (open to any caller), `setPoolAdminFees` (pool admin), and `setPoolProtocolFee` (factory owner) all route through this same `collectFees` call:

```solidity
function collectPoolFees(address pool) external override nonReentrant {
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool).collectFees(
        c.protocolSpreadFeeE6, c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8, c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
    );
}
``` [2](#0-1) 

```solidity
IMetricOmmPoolCollectFees(pool).collectFees(
    c.protocolSpreadFeeE6, c.adminSpreadFeeE6,
    c.protocolNotionalFeeE8, c.adminNotionalFeeE8,
    poolAdminFeeDestination[pool]
);
``` [3](#0-2) [4](#0-3) 

Because `setPoolAdminFees` and `setPoolProtocolFee` call `collectFees` as a mandatory first step before writing new fee rates, a blocked `collectFees` also freezes all fee-rate updates for that pool.

---

### Impact Explanation

- **Protocol fees locked**: Accrued spread and notional fees sit in the pool and cannot be transferred to the FACTORY or the admin destination.
- **Fee-rate management bricked**: `setPoolAdminFees` and `setPoolProtocolFee` both revert, so neither the pool admin nor the factory owner can adjust fee rates for the affected pool until the blockage is resolved.
- **`collectPoolFees` DoS**: Any caller attempting to trigger fee collection is blocked.

The `notionalFeeToken0Scaled` / `notionalFeeToken1Scaled` counters are preserved on revert (no permanent accounting corruption), but the spread-fee surplus also cannot be distributed because it is computed inline and never separately tracked.

---

### Likelihood Explanation

- USDC and USDT both implement address blacklists; this is explicitly in scope.
- `adminFeeDestination` is set by the pool admin and can be any address. An address that is valid at pool creation time may later be blacklisted (e.g., due to regulatory action or a compromised key).
- The pool admin can call `setPoolAdminFeeDestination` to rotate the destination without triggering `collectFees`, so the blockage is recoverable — but only if the pool admin acts promptly and in good faith. A pool admin who is unresponsive, compromised, or adversarial can sustain the DoS indefinitely.
- No privileged escalation is required: the trigger is a standard USDC blacklist event combined with a valid (non-zero) `adminFeeDestination`.

---

### Recommendation

Isolate each transfer so that a failure in one leg does not block the others. Two approaches:

1. **Wrap each transfer in a try/catch** and emit an event on failure, allowing partial collection and retrying the failed leg later.
2. **Separate token and recipient selection** (as the Clober fix did): add a `token` and `recipient` parameter so callers can collect one token to one recipient at a time, keeping independent accounting for each leg.

Additionally, consider separating the notional-fee counter reset per token so that a successful token1 collection can still zero `notionalFeeToken1Scaled` even if the token0 leg fails.

---

### Proof of Concept

1. Pool is deployed with `token0 = USDC`, `token1 = WETH`.
2. Pool admin sets `adminFeeDestination` to address `A` (valid at the time).
3. USDC blacklists address `A` (e.g., regulatory freeze).
4. Swaps accrue spread and notional fees in the pool.
5. Anyone calls `MetricOmmPoolFactory.collectPoolFees(pool)`.
   - Factory calls `pool.collectFees(...)` with `adminFeeDestination = A`.
   - `transferToken0(A, totalFee0ToAdmin)` → USDC `safeTransfer` reverts (blacklisted).
   - Entire `collectFees` reverts; `notionalFeeToken0Scaled` and `notionalFeeToken1Scaled` are unchanged.
6. Factory owner calls `setPoolProtocolFee(pool, newFee, newNotional)` to adjust rates.
   - Same `collectFees` call is made first → same revert.
   - Fee-rate update is blocked.
7. Fees continue to accumulate but cannot be distributed; fee-rate management is frozen until the pool admin calls `setPoolAdminFeeDestination(pool, nonBlacklistedAddress)`.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L416-430)
```text
      if (totalFee0ToAdmin > 0) {
        transferToken0(adminFeeDestination_, totalFee0ToAdmin);
      }
      if (totalFee1ToAdmin > 0) {
        transferToken1(adminFeeDestination_, totalFee1ToAdmin);
      }
      if (totalFee0ToProtocol > 0) {
        transferToken0(FACTORY, totalFee0ToProtocol);
      }
      if (totalFee1ToProtocol > 0) {
        transferToken1(FACTORY, totalFee1ToProtocol);
      }

      notionalFeeToken0Scaled = 0;
      notionalFeeToken1Scaled = 0;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L328-335)
```text
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L379-389)
```text
  function collectPoolFees(address pool) external override nonReentrant {
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L418-425)
```text
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );
```
