### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks position `owner` but silently discards the actual caller, allowing any unprivileged address to bypass the deposit allowlist by nominating an allowlisted address as `owner` — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` is the sole gate that enforces the curated-pool deposit policy. It checks the `owner` argument (the position recipient) against the per-pool allowlist, but `MetricOmmPool.addLiquidity` imposes no requirement that `msg.sender == owner`. Any unprivileged caller can therefore invoke `addLiquidity` with an allowlisted address as `owner`, pay the tokens themselves via the callback, and cause the allowlisted address to receive LP shares — bypassing the allowlist entirely.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes the actual caller as the first argument to `_beforeAddLiquidity` and the caller-supplied `owner` as the second:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

The pool imposes no `msg.sender == owner` check on `addLiquidity` (contrast with `removeLiquidity` at line 206 which does enforce `if (msg.sender != owner) revert NotPositionOwner()`). [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives the actual caller as its first (unnamed, discarded) argument and `owner` as its second. It only checks `owner`:

```solidity
// DepositAllowlistExtension.sol lines 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

The actual caller — the address whose tokens enter the pool — is the first argument but is unnamed and never read. The allowlist check therefore passes whenever `owner` is allowlisted, regardless of who is actually calling and paying.

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)` further exposes this: it accepts an arbitrary `owner` with only a non-zero check, stores `msg.sender` as the payer in transient storage, and forwards the caller-supplied `owner` directly to the pool:

```solidity
// MetricOmmPoolLiquidityAdder.sol lines 56-68
function addLiquidityExactShares(
    address pool, address owner, uint80 salt, ...
) external payable override returns (...) {
    _validateOwner(owner);   // only checks owner != address(0)
    ...
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [4](#0-3) 

---

### Impact Explanation

A non-allowlisted user can deposit tokens into a curated pool by specifying any allowlisted address as `owner`. The attacker's tokens enter the pool; the allowlisted address receives LP shares it did not request. The pool admin's curation policy — the sole mechanism for controlling who participates in the pool — is silently violated. This is an admin-boundary break: an unprivileged path bypasses the factory-configured extension guard that the pool admin deployed to restrict depositors.

---

### Likelihood Explanation

The attack requires only knowledge of one allowlisted address (publicly readable from `allowedDepositor`) and willingness to pay tokens. No special role, flash loan, or oracle manipulation is needed. The path is reachable both directly through `MetricOmmPool.addLiquidity` and through the supported `MetricOmmPoolLiquidityAdder` periphery. Any user can trigger it at any time on any pool that has `DepositAllowlistExtension` configured.

---

### Recommendation

The extension should gate on the actual caller, not only on `owner`. The actual caller is already forwarded as the first argument by the pool. Change the check to validate the caller:

```solidity
function beforeAddLiquidity(address caller, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender]
        && !allowedDepositor[msg.sender][caller]   // gate the actual payer/initiator
        && !allowedDepositor[msg.sender][owner]) { // optionally also gate the owner
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Alternatively, `MetricOmmPool.addLiquidity` could enforce `msg.sender == owner` (mirroring `removeLiquidity`), but this would break the `MetricOmmPoolLiquidityAdder` design where the adder is the caller and the user is the owner.

---

### Proof of Concept

1. Pool admin deploys a curated pool with `DepositAllowlistExtension`. Bob (`0xBob`) is allowlisted; Alice (`0xAlice`) is not.
2. Alice calls `pool.addLiquidity(0xBob, salt, deltas, callbackData, extensionData)` directly.
3. The pool calls `_beforeAddLiquidity(0xAlice, 0xBob, ...)`, which calls `DepositAllowlistExtension.beforeAddLiquidity(0xAlice, 0xBob, ...)`.
4. The extension checks `allowedDepositor[pool][0xBob]` → `true`. The guard passes.
5. `LiquidityLib.addLiquidity` mints LP shares to `0xBob`.
6. The pool calls `IMetricOmmSwapCallback(0xAlice).metricOmmModifyLiquidityCallback(...)`. Alice's contract pays the required tokens.
7. Result: Alice's tokens are now in the pool; Bob holds LP shares he did not request. The allowlist has been bypassed — Alice's capital entered a pool she was explicitly excluded from. [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L12-42)
```text
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }

  function setAllowAllDepositors(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllDepositors[pool_] = allowed;
    emit AllowAllDepositorsSet(pool_, allowed);
  }

  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
  }

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
