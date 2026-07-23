### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any unprivileged address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook receives two address arguments: `sender` (the actual `msg.sender` of the pool call, i.e., the entity paying tokens) and `owner` (a caller-supplied position beneficiary). The implementation silently discards `sender` and checks only `owner`. Because `owner` is a free parameter that any caller can set to any address, any unlisted address can bypass the allowlist by naming an allowlisted address as `owner`.

---

### Finding Description

In `MetricOmmPool.addLiquidity`, the pool passes `msg.sender` as `sender` and the caller-supplied `owner` as the position beneficiary to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

There is no constraint that `msg.sender == owner`; the pool deliberately allows a third party to add liquidity on behalf of any `owner`.

The extension hook signature receives both addresses:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
```

but the first parameter (`sender`) is unnamed and never read. The guard only checks `owner`:

```solidity
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
}
``` [2](#0-1) 

An unlisted address can therefore call `pool.addLiquidity(allowlisted_address, salt, deltas, ...)`. The extension sees `owner = allowlisted_address`, the check passes, and the unlisted address pays the tokens via the swap callback while the position is credited to the allowlisted address.

By contrast, `SwapAllowlistExtension` correctly checks `sender` (the actual caller) and ignores `recipient`:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [3](#0-2) 

The asymmetry confirms the deposit extension has the wrong field.

---

### Impact Explanation

The deposit allowlist is the primary access-control mechanism for restricted pools (e.g., KYC/AML-gated, institutional-only, or whitelist-only liquidity pools). Bypassing it allows any address to:

1. Add liquidity to a pool that should reject them, breaking the admin-enforced access boundary.
2. Alter pool depth and bin composition without authorization, potentially diluting existing LPs or shifting the pool's liquidity profile in ways the admin did not sanction.
3. Circumvent compliance requirements the pool admin intended to enforce.

This is a direct admin-boundary break: an unprivileged path (supplying an allowlisted `owner`) defeats a factory-configured guard with no special privilege required.

---

### Likelihood Explanation

Exploitation requires only a standard `addLiquidity` call with a known allowlisted address as `owner`. No privileged role, flash loan, or oracle manipulation is needed. Any address that can observe the allowlist (public mapping) can trigger this immediately.

---

### Recommendation

Check `sender` (the actual depositor/payer) instead of `owner` (the position beneficiary):

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

If the intent is to gate both the payer and the beneficiary, both `sender` and `owner` should be checked.

---

### Proof of Concept

```
Setup:
  - Pool P is configured with DepositAllowlistExtension.
  - allowedDepositor[P][alice] = true  (alice is allowlisted)
  - bob is NOT in the allowlist

Attack:
  1. bob calls P.addLiquidity(owner=alice, salt=0, deltas=..., callbackData=..., extensionData=...)
  2. Pool calls extension.beforeAddLiquidity(sender=bob, owner=alice, ...)
  3. Extension checks allowedDepositor[P][alice] → true → no revert
  4. Pool calls LiquidityLib.addLiquidity(..., owner=alice, ...)
  5. Pool calls bob.metricOmmSwapCallback(...) → bob transfers tokens to pool
  6. Position shares are minted to alice

Result: bob (unlisted) has added liquidity to a restricted pool.
         alice can later call removeLiquidity to recover the tokens.
         The allowlist is fully bypassed.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-40)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
```
