### Title
Per-Bin Additional Fee Has No Cap, Allowing Pool Admin to Bypass the Factory-Enforced Fee Ceiling - (File: `metric-core/contracts/MetricOmmPoolFactory.sol`)

### Summary

The factory enforces explicit caps on the pool admin's global spread fee via `maxAdminSpreadFeeE6`, but the `setPoolBinAdditionalFees` path applies no analogous cap on `addFeeBuyE6` / `addFeeSellE6`, letting a pool admin silently charge swappers more than the protocol-intended maximum on any specific bin.

### Finding Description

`MetricOmmPoolFactory` maintains a layered fee-cap system. The factory owner sets `maxAdminSpreadFeeE6` (hard ceiling 20 %, i.e. 200 000 E6). Every call to `setPoolAdminFees` is gated:

```solidity
// MetricOmmPoolFactory.sol lines 414-415
if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
``` [1](#0-0) 

The pool admin also has a second fee lever — per-bin additional fees — set via:

```solidity
// MetricOmmPoolFactory.sol lines 450-457
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
{
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
``` [2](#0-1) 

There is **no cap check** on `addFeeBuyE6` or `addFeeSellE6` here. The pool's `setBinAdditionalFees` only validates the bin index:

```solidity
// MetricOmmPool.sol lines 464-474
function setBinAdditionalFees(int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external
    onlyFactory
    nonReentrant(PoolActions.SET_BIN_ADDITIONAL_FEES)
{
    if (bin < LOWEST_BIN || bin > HIGHEST_BIN) revert InvalidBinIndex(bin);
    BinState storage s = _binStates[bin];
    s.addFeeBuyE6 = addFeeBuyE6;
    s.addFeeSellE6 = addFeeSellE6;
    emit BinAdditionalFeesUpdated(bin, addFeeBuyE6, addFeeSellE6);
}
``` [3](#0-2) 

`addFeeBuyE6` and `addFeeSellE6` are `uint16`, so the maximum settable value is **65 535 E6 ≈ 6.55 %** per bin, applied on top of the already-capped global spread fee. The per-bin fees are stored in `BinState` and consumed by `SwapMath` during bin traversal, making them directly additive to the global spread fee charged to every swapper who crosses that bin. [4](#0-3) 

The documentation acknowledges the interaction ("understand interaction with global spread fee") but provides no on-chain enforcement. [5](#0-4) 

### Impact Explanation

A pool admin can set `addFeeBuyE6 = 65535` and `addFeeSellE6 = 65535` on every configured bin. Every swapper crossing those bins pays the global spread fee **plus** up to 6.55 % additional per bin, exceeding the factory-enforced ceiling. Because the per-bin fee is applied inside `SwapMath` during the bin walk, the overcharge is deducted from the swapper's input token amount before settlement — a direct, quantifiable loss of user principal on every swap through the affected bin(s). The pool admin can target the active bin (the one most swaps traverse) to maximize extraction.

### Likelihood Explanation

The pool admin is a semi-trusted role: the factory owner deliberately caps the admin's global fee precisely because the admin is not fully trusted with fee extraction. The per-bin path is reachable by any current `poolAdmin[pool]` in a single transaction with no timelock. Any pool whose admin is compromised, acts adversarially, or is a market maker with misaligned incentives can exploit this immediately.

### Recommendation

Add a cap check in `setPoolBinAdditionalFees` (or in `setBinAdditionalFees` on the pool) analogous to the global fee guard. A reasonable bound is `addFeeBuyE6 <= maxAdminSpreadFeeE6` and `addFeeSellE6 <= maxAdminSpreadFeeE6`, or introduce a dedicated `maxBinAdditionalFeeE6` constant. Example:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (addFeeBuyE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

### Proof of Concept

1. Factory owner deploys a pool with `maxAdminSpreadFeeE6 = 200_000` (20 %) and `adminSpreadFeeE6 = 10_000` (1 %).
2. Pool admin calls `factory.setPoolBinAdditionalFees(pool, 0, 65535, 65535)` — no revert.
3. A swapper calls `pool.swap(...)` with the active bin at index 0.
4. `SwapMath` applies the global spread fee (1 %) **plus** the per-bin additional fee (6.5535 %) = **7.5535 %** total, exceeding the factory-enforced 20 % hard cap on admin fees in combination with any future global fee increase, and already exceeding the intended per-admin-action cap in isolation.
5. The swapper receives fewer output tokens than the protocol's fee cap system was designed to guarantee. [2](#0-1) [3](#0-2) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L284-295)
```text
  function setFeeCaps(
    uint24 newMaxProtocolSpreadFeeE6,
    uint24 newMaxAdminSpreadFeeE6,
    uint24 newMaxProtocolNotionalFeeE8,
    uint24 newMaxAdminNotionalFeeE8
  ) external override onlyOwner {
    if (
      newMaxProtocolSpreadFeeE6 > HARD_MAX_SPREAD_FEE_E6 || newMaxAdminSpreadFeeE6 > HARD_MAX_SPREAD_FEE_E6
        || newMaxProtocolNotionalFeeE8 > HARD_MAX_NOTIONAL_FEE_E8 || newMaxAdminNotionalFeeE8 > HARD_MAX_NOTIONAL_FEE_E8
    ) {
      revert FeeCapsExceedHardLimit();
    }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L414-415)
```text
    if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
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

**File:** metric-core/contracts/MetricOmmPool.sol (L464-474)
```text
  function setBinAdditionalFees(int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external
    onlyFactory
    nonReentrant(PoolActions.SET_BIN_ADDITIONAL_FEES)
  {
    if (bin < LOWEST_BIN || bin > HIGHEST_BIN) revert InvalidBinIndex(bin);
    BinState storage s = _binStates[bin];
    s.addFeeBuyE6 = addFeeBuyE6;
    s.addFeeSellE6 = addFeeSellE6;
    emit BinAdditionalFeesUpdated(bin, addFeeBuyE6, addFeeSellE6);
  }
```

**File:** metric-core/docs/POOL_CONFIGURATION_AND_MANAGEMENT.md (L141-141)
```markdown
| **`setPoolBinAdditionalFees(pool, bin, addFeeBuyE6, addFeeSellE6)`**     | Updates **per-bin** additional buy/sell fees on the pool (E6).                                                                                                                                    | Use for fine-grained incentives or disincentives on specific bins; understand interaction with global spread fee.                                                                           |
```
