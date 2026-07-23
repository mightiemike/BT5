### Title
Pool Admin Bypasses Hard Fee Cap via Uncapped Per-Bin Additional Fees — (`metric-core/contracts/MetricOmmPoolFactory.sol`)

---

### Summary

`MetricOmmPoolFactory.setPoolBinAdditionalFees` forwards per-bin additional spread fees directly to the pool with no cap validation, while every other fee-setter in the factory enforces `maxAdminSpreadFeeE6` / `HARD_MAX_SPREAD_FEE_E6`. A pool admin can therefore push the effective per-bin swap fee above the protocol-enforced hard cap of 20 % (`200_000 E6`), causing traders to pay more than the cap permits.

---

### Finding Description

The factory defines two hard limits and enforces them on every base-fee setter:

```
HARD_MAX_SPREAD_FEE_E6  = 200_000   // 20 %
HARD_MAX_NOTIONAL_FEE_E8 = 1_000_000 // 1 %
```

`setPoolAdminFees` correctly rejects any admin spread fee above `maxAdminSpreadFeeE6`:

```solidity
// MetricOmmPoolFactory.sol  lines 414-415
if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
```

`setPoolBinAdditionalFees`, however, performs **no cap check at all** before forwarding to the pool:

```solidity
// MetricOmmPoolFactory.sol  lines 450-457
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

`addFeeBuyE6` and `addFeeSellE6` are `uint16` (max 65 535 = **6.5535 % in E6 units**). The interface documents them as fees applied *"on top of base spread"*:

```solidity
// IMetricOmmPoolFactoryActions.sol  lines 52-56
/// @param addFeeBuyE6  Additional fee on buys into the bin (E6).
/// @param addFeeSellE6 Additional fee on sells out of the bin (E6).
function setBinAdditionalFees(int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6) external;
```

Because these values are additive to the base spread fee during swap execution, the effective per-bin fee is:

```
effectiveFee = spreadFeeE6 + addFeeBuyE6   (or addFeeSellE6)
```

A pool admin who has already set `adminSpreadFeeE6 = 200_000` (the hard cap) can additionally set `addFeeBuyE6 = 65_535` on every bin, yielding an effective buy-side fee of **26.5535 %** — 6.5535 percentage points above the hard cap — with no revert anywhere in the call path.

---

### Impact Explanation

Every swap through an affected bin deducts more from the trader's input (or delivers less output) than the protocol's hard cap permits. The excess fee accrues to the pool admin's fee destination. This is a direct, quantifiable loss of trader principal on every swap touching the manipulated bin. The loss is proportional to swap volume and can be sustained indefinitely until the factory owner intervenes.

---

### Likelihood Explanation

The pool admin role is assigned at pool creation by the pool creator and is transferable. It is a semi-trusted role explicitly constrained by the fee-cap system. Any pool admin — whether malicious from the start, compromised, or acting in bad faith — can call `setPoolBinAdditionalFees` at any time with no timelock, no cap guard, and no factory-owner approval. The call requires only `onlyPoolAdmin`, which is a single-address check with no additional friction.

---

### Recommendation

Add the same cap validation to `setPoolBinAdditionalFees` that exists in `setPoolAdminFees`. Define a hard cap for per-bin additional fees (e.g., `HARD_MAX_BIN_ADD_FEE_E6`) and enforce it:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (addFeeBuyE6 > HARD_MAX_BIN_ADD_FEE_E6) revert BinFeeTooHigh();
    if (addFeeSellE6 > HARD_MAX_BIN_ADD_FEE_E6) revert BinFeeTooHigh();
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

Alternatively, enforce that `spreadFeeE6 + addFeeBuyE6 ≤ HARD_MAX_SPREAD_FEE_E6` at the time of the call.

---

### Proof of Concept

1. Factory owner deploys a pool; pool admin is set to `attacker`.
2. `attacker` calls `setPoolAdminFees(pool, 200_000, 0)` — sets base admin spread fee to the hard cap (20 %).
3. `attacker` calls `setPoolBinAdditionalFees(pool, 0, 65_535, 65_535)` — no revert; pool stores `addFeeBuyE6 = 65_535` for bin 0.
4. A trader swaps through bin 0. The effective buy-side fee applied is `200_000 + 65_535 = 265_535 E6` (≈ 26.55 %), exceeding the hard cap of `200_000 E6` (20 %).
5. The excess 6.55 % of the trader's input is captured as fee and routed to `poolAdminFeeDestination`, constituting a direct loss of trader principal above the protocol-guaranteed maximum.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L43-45)
```text
  /// @dev Owner `setFeeCaps` values cannot exceed these (spread: 1e6 = 100%; notional: 1e8 = 100%)
  uint24 internal constant HARD_MAX_SPREAD_FEE_E6 = 200_000;
  uint24 internal constant HARD_MAX_NOTIONAL_FEE_E8 = 1_000_000;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L413-415)
```text
  {
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolFactoryActions.sol (L52-56)
```text
  /// @notice Set per-bin additional buy and sell spread fees in E6 on top of base spread.
  /// @param bin Bin index within the pool configured bin range.
  /// @param addFeeBuyE6 Additional fee on buys into the bin (E6).
  /// @param addFeeSellE6 Additional fee on sells out of the bin (E6).
  function setBinAdditionalFees(int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6) external;
```
