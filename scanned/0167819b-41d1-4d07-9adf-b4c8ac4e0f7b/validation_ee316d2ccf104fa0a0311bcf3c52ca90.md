### Title
Pool Admin Can Instantly Change Fees Without Timelock, Enabling Frontrunning of Large Swaps — (File: `metric-core/contracts/MetricOmmPoolFactory.sol`)

---

### Summary

`MetricOmmPoolFactory.setPoolAdminFees` and `setPoolBinAdditionalFees` allow the pool admin to change swap fees with immediate on-chain effect and no timelock. Because any user can create a pool and self-assign as admin, a malicious pool admin can monitor the mempool, raise fees to the configured maximum immediately before a large pending swap, collect the inflated fee, and restore the original rate — the exact frontrunning pattern from the FERC1155 royalty report.

---

### Finding Description

`setPoolAdminFees` enforces caps (`maxAdminSpreadFeeE6`, `maxAdminNotionalFeeE8`) but applies the new fee atomically in the same transaction with no pending/execute two-step or timelock:

```solidity
// metric-core/contracts/MetricOmmPoolFactory.sol  lines 408-435
function setPoolAdminFees(address pool, uint24 newAdminSpreadFeeE6, uint24 newAdminNotionalFeeE8)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
    // ... collects accrued fees, then immediately writes new rate and pushes to pool
    IMetricOmmPoolFactoryActions(pool)
      .setPoolFees(c.protocolSpreadFeeE6 + c.adminSpreadFeeE6, ...);
}
```

`setPoolBinAdditionalFees` is worse: it has **no cap check at all** in the factory (only the implicit `uint16` ceiling of 65 535 / 1e6 ≈ 6.55 %) and no timelock:

```solidity
// metric-core/contracts/MetricOmmPoolFactory.sol  lines 450-457
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

Contrast this with the price-provider rotation, which correctly uses a propose/execute pattern with a per-pool timelock:

```solidity
// metric-core/contracts/MetricOmmPoolFactory.sol  lines 487-490
uint256 executeAfter = block.timestamp + timelock;
pendingPriceProvider[pool] = newPriceProvider;
pendingPriceProviderExecuteAfter[pool] = executeAfter;
```

And the `OracleValueStopLossExtension` drawdown/decay/watermark setters, which all go through `_afterTimelock(pool_)` before taking effect. Fee mutation is the only privileged setter that skips this protection entirely.

The admin fee destination (`poolAdminFeeDestination[pool]`) is also changeable instantly by the pool admin via `setPoolAdminFeeDestination`, confirming that the admin fully controls where inflated fees are routed.

---

### Impact Explanation

A malicious pool admin can extract the maximum allowed admin fee from any large swap without the trader's knowledge:

1. Admin sets `adminSpreadFeeE6 = 0` at pool creation to attract volume.
2. A large swap appears in the mempool.
3. Admin frontruns it with `setPoolAdminFees(pool, maxAdminSpreadFeeE6, maxAdminNotionalFeeE8)`.
4. The swap settles at the inflated rate; admin collects the difference via `collectPoolFees`.
5. Admin immediately calls `setPoolAdminFees(pool, 0, 0)` to restore the advertised rate.

For `setPoolBinAdditionalFees`, the same attack applies per-bin with no factory-level cap, so the admin can push the active bin's buy/sell fee to the `uint16` ceiling (≈ 6.55 %) on top of the spread fee, compounding the extraction.

The loss is direct and quantifiable: `(maxAdminSpreadFeeE6 / 1e6) × swapNotional` per sandwiched transaction, repeatable on every large swap.

---

### Likelihood Explanation

- **Unprivileged trigger**: Any address can call `factory.createPool(params)` with `params.admin = self`, making the pool admin role freely obtainable.
- **No special knowledge**: The attack only requires watching the public mempool for large pending swaps.
- **No timelock to race**: The fee change and the victim swap can land in the same block; the admin simply submits with a higher gas price.
- **Precedent**: The external FERC1155 report describes the identical pattern (1 % → 100 % royalty frontrun) and was accepted as Medium.

---

### Recommendation

1. **Add a propose/execute timelock to `setPoolAdminFees`** using the same `priceProviderTimelock[pool]` already stored per pool, mirroring `proposePoolPriceProvider` / `executePoolPriceProviderUpdate`.
2. **Add an explicit cap check to `setPoolBinAdditionalFees`** in the factory (e.g., against `maxAdminSpreadFeeE6`) before forwarding to the pool, consistent with the cap enforcement in `setPoolAdminFees`.
3. Consider emitting a pending-fee event so off-chain monitors and traders can detect imminent fee changes before they execute.

---

### Proof of Concept

```solidity
// Scenario: Alice is pool admin; Bob submits a large swap

// Step 1 – Alice creates pool with zero admin fee
PoolParameters memory params = _defaultParams();
params.admin = alice;
params.adminSpreadFeeE6 = 0;
address pool = factory.createPool(params);

// Step 2 – Bob's large swap is visible in the mempool (100 000 token0)
// Alice frontruns it:

vm.prank(alice);
factory.setPoolAdminFees(pool, factory.maxAdminSpreadFeeE6(), factory.maxAdminNotionalFeeE8());
// Fee change is live immediately — no timelock, no pending state

// Step 3 – Bob's swap executes at the inflated fee
// (Bob submitted with normal gas; Alice used higher gas to land first)
vm.prank(bob);
pool.swap(bob, false, int128(100_000e18), type(uint128).max, ...);

// Step 4 – Alice collects the inflated admin fee
factory.collectPoolFees(pool);

// Step 5 – Alice restores the advert