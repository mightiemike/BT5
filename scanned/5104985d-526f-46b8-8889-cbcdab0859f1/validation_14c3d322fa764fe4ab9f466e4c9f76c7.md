### Title
`DepositAllowlistExtension` gates the position *owner* instead of the depositing *payer*, allowing any unprivileged caller to bypass the deposit allowlist — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` checks the `owner` argument (the LP-position recipient) against the per-pool allowlist, but the economically relevant actor is the *payer* — the address that actually transfers tokens into the pool via the callback. Because `MetricOmmPoolLiquidityAdder` exposes an overload that lets the caller supply an arbitrary `owner` separate from `msg.sender`, any unprivileged address can bypass the deposit gate by nominating an already-allowlisted address as the position owner.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` is documented as gating `addLiquidity` **by depositor address**, but its implementation checks `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The pool's `addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as the position owner to `_beforeAddLiquidity`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

`MetricOmmPoolLiquidityAdder` exposes an overload that accepts an explicit `owner` distinct from `msg.sender` (the payer):

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol L56-68
function addLiquidityExactShares(
    address pool,
    address owner,          // ← caller-controlled, separate from msg.sender
    uint80 salt,
    LiquidityDelta calldata deltas,
    ...
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [3](#0-2) 

`_addLiquidity` stores `msg.sender` as the payer in transient context and calls `pool.addLiquidity(positionOwner=owner, ...)`:

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol L194-196
_setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
try IMetricOmmPoolActions(pool)
    .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData)
``` [4](#0-3) 

The extension therefore evaluates `allowedDepositor[pool][owner]` — the allowlisted recipient — while the actual token transfer originates from the non-allowlisted `msg.sender`. The same bypass applies to direct `pool.addLiquidity(owner=allowlistedAddress, ...)` calls, since the pool imposes no constraint that `owner == msg.sender`.

---

### Impact Explanation

An unprivileged address can deposit tokens into an allowlist-gated pool without being on the allowlist. The pool receives unauthorized liquidity, violating the admin's access-control intent. The allowlisted address receives an LP position it did not request and may not want; the attacker's tokens are locked in that position. This constitutes a broken core pool guard: the deposit allowlist — the only mechanism preventing unauthorized liquidity provision — is fully bypassed by any caller who knows a single allowlisted address, which is public on-chain state.

---

### Likelihood Explanation

The attack requires no special privilege. The allowlisted addresses are readable from `allowedDepositor` storage. Any user can call the public `addLiquidityExactShares(pool, owner=knownAllowlistedAddress, ...)` overload on the `MetricOmmPoolLiquidityAdder`. No admin cooperation, no flash loan, and no price manipulation is needed.

---

### Recommendation

Change `beforeAddLiquidity` to gate on `sender` (the direct caller of `pool.addLiquidity`) rather than `owner`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

For the `MetricOmmPoolLiquidityAdder` path, the pool admin should allowlist the adder contract only if all users of the adder are trusted, or the adder should forward the original `msg.sender` through `extensionData` so the extension can gate on the true initiator.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension`; admin allowlists `Alice` and no one else.
2. `Bob` (not allowlisted) calls:
   ```solidity
   liquidityAdder.addLiquidityExactShares(
       pool,
       owner = Alice,   // allowlisted address
       salt,
       deltas,
       maxAmount0, maxAmount1,
       extensionData
   );
   ```
3. The adder calls `pool.addLiquidity(owner=Alice, ...)` with `msg.sender = liquidityAdder`.
4. The pool calls `_beforeAddLiquidity(sender=liquidityAdder, owner=Alice, ...)`.
5. `ExtensionCalling._callExtensionsInOrder` invokes `DepositAllowlistExtension.beforeAddLiquidity(sender=liquidityAdder, owner=Alice, ...)`.
6. The extension evaluates `allowedDepositor[pool][Alice]` → `true`. No revert.
7. `LiquidityLib.addLiquidity` mints shares to `Alice`; the callback pulls tokens from `Bob` (the payer stored in transient context).
8. Bob's tokens are now in the pool under Alice's position. The deposit allowlist was never enforced against Bob.

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L193-196)
```text
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```
