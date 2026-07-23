### Title
Pool admin can set uncapped per-bin additional fees via `setPoolBinAdditionalFees`, bypassing the configured fee-cap system — (File: `metric-core/contracts/MetricOmmPoolFactory.sol`)

---

### Summary

`MetricOmmPoolFactory.setPoolBinAdditionalFees` forwards raw `uint16` per-bin fee values directly to the pool with **no upper-bound validation**, while every other admin fee setter in the same contract enforces `maxAdminSpreadFeeE6`. A malicious or compromised pool admin can front-run a pending swap by atomically raising the per-bin additional fee to `type(uint16).max` (65 535 E6 ≈ 6.55 %), then letting the swap execute through that bin, extracting far more from the trader than they agreed to.

---

### Finding Description

`MetricOmmPoolFactory` enforces a layered fee-cap system. The factory owner sets hard caps via `setFeeCaps`, which are bounded by `HARD_MAX_SPREAD_FEE_E6 = 200_000` (20 %). Pool-level admin fees are then constrained by `maxAdminSpreadFeeE6` inside `setPoolAdminFees`:

```solidity
// MetricOmmPoolFactory.sol lines 414-415
if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
``` [1](#0-0) 

However, the sibling function `setPoolBinAdditionalFees` performs **no such check**:

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

The `addFeeBuyE6` and `addFeeSellE6` parameters are `uint16`, whose maximum value is **65 535**, representing 6.5535 % in E6 units. There is no guard comparing these values against `maxAdminSpreadFeeE6` before the call is forwarded to the pool. The pool's `setBinAdditionalFees` interface also declares no internal cap:

```solidity
// IMetricOmmPoolFactoryActions.sol line 56
function setBinAdditionalFees(int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6) external;
``` [3](#0-2) 

Additionally, unlike `setPoolAdminFees` (which calls `collectFees` before updating to settle accrued fees at the old rate), `setPoolBinAdditionalFees` skips fee collection entirely, meaning the fee change takes effect immediately on the next swap with no settlement checkpoint. [4](#0-3) 

---

### Impact Explanation

**High.** A pool admin can front-run any pending swap targeting a specific bin by calling `setPoolBinAdditionalFees(pool, targetBin, 65535, 65535)` in the same block (or via a bundle). The swap executes with an additional 6.5535 % fee on top of the base spread fee, directly extracting value from the trader's principal. The excess fee accrues to the pool's fee balance and is later collected by the admin via `collectPoolFees`. This is a direct, quantifiable loss of user principal with no recovery path.

---

### Likelihood Explanation

**Low.** Exploitation requires a malicious or compromised pool admin. Pool admins are semi-trusted actors (not the factory owner), but they are permissioned and known at pool creation time. The attack is however trivially executable once the admin is compromised — it requires a single transaction with no timelock.

---

### Recommendation

Add an upper-bound check in `setPoolBinAdditionalFees` mirroring the pattern used in `setPoolAdminFees`:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (addFeeBuyE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

Optionally, introduce a dedicated `maxAdminBinAdditionalFeeE6` cap (separate from the aggregate spread cap) so users have a clear, auditable upper bound on per-bin surcharges.

---

### Proof of Concept

1. Alice submits a swap of 100 USDC → ETH through a pool, expecting the advertised 0.1 % spread fee. Her transaction is in the mempool.
2. The pool admin observes Alice's pending transaction and identifies the target bin (e.g., bin `0`).
3. The pool admin front-runs Alice by calling:
   ```solidity
   factory.setPoolBinAdditionalFees(pool, 0, 65535, 65535);
   // addFeeBuyE6 = 65535 → +6.5535% additional fee on buys into bin 0
   ``` [2](#0-1) 
4. Alice's swap executes through bin 0 with an effective fee of 0.1 % + 6.5535 % = 6.6535 %, paying ~6.55 USDC more in fees than expected.
5. The admin calls `collectPoolFees` to extract the excess fee balance.
6. The admin resets the bin fee to 0 to avoid detection.

No privileged factory-owner action is required; the pool admin role alone is sufficient. No malicious token or non-standard ERC20 behavior is involved.

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L414-415)
```text
    if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L417-425)
```text
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolFactoryActions.sol (L56-56)
```text
  function setBinAdditionalFees(int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6) external;
```
