### Title
`DepositAllowlistExtension.beforeAddLiquidity` gates on `owner` (position beneficiary) instead of `sender` (actual depositor), allowing any non-allowlisted address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` is supposed to restrict which addresses may deposit into a curated pool. However, it checks the `owner` argument (the position beneficiary) rather than the `sender` argument (the address that actually called `addLiquidity` and will pay the tokens). Because `owner` is a freely chosen caller-supplied parameter, any non-allowlisted address can bypass the guard by specifying an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` parameter (who receives the LP position) and passes `msg.sender` as `sender` to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

The extension hook signature receives both actors:

```
beforeAddLiquidity(address sender, address owner, ...)
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently discards `sender` (first parameter, unnamed `address`) and checks only `owner` (second parameter):

```solidity
// DepositAllowlistExtension.sol line 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

`owner` is a free parameter supplied by the caller — it is not authenticated by the pool. The pool only enforces `msg.sender == owner` for `removeLiquidity`, not for `addLiquidity`:

```solidity
// MetricOmmPool.sol line 206
if (msg.sender != owner) revert NotPositionOwner();  // only in removeLiquidity
``` [4](#0-3) 

There is no equivalent constraint in `addLiquidity`. Any address may call `pool.addLiquidity(allowlistedAddress, salt, ...)`, pay the tokens via the callback, and have the LP position credited to the allowlisted address. The extension sees `owner = allowlistedAddress` and passes the check.

Contrast with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the address that called `pool.swap()`):

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [5](#0-4) 

The deposit extension applies the wrong identity check, making the allowlist entirely ineffective.

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for curating who may provide liquidity to a pool. With this bug, the allowlist enforces nothing: any non-allowlisted address can deposit by specifying any allowlisted address as `owner`. The depositor pays the tokens; the allowlisted address receives LP shares it did not request. The pool admin's curation policy is completely bypassed. This constitutes a broken core pool functionality (allowlist guard fails open) and an admin-boundary break where an unprivileged path circumvents a configured protection.

---

### Likelihood Explanation

The bypass requires only a single direct call to `pool.addLiquidity` with a known allowlisted address as `owner`. No special permissions, flash loans, or multi-step setup are needed. Any address that can observe the allowlist state (public mapping) can execute the bypass immediately. The `MetricOmmPoolLiquidityAdder` periphery path also reaches the same code path, as it passes `positionOwner` directly to `pool.addLiquidity`:

```solidity
// MetricOmmPoolLiquidityAdder.sol line 194-196
IMetricOmmPoolActions(pool).addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData)
``` [6](#0-5) 

The `_validateOwner` check in the adder only rejects `address(0)`, not non-allowlisted callers:

```solidity
// MetricOmmPoolLiquidityAdder.sol line 247-249
function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
}
``` [7](#0-6) 

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositor/payer) instead of `owner` (the position beneficiary):

```solidity
// Fix: check sender, not owner
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This aligns with how `SwapAllowlistExtension` correctly gates on `sender` for swaps. Pool admins who need to allowlist by position owner rather than depositor should use a separate, explicitly documented extension.

---

### Proof of Concept

1. Pool admin deploys a curated pool with `DepositAllowlistExtension` attached.
2. Pool admin calls `extension.setAllowedToDeposit(pool, bob, true)` — only `bob` is allowlisted.
3. `alice` (not allowlisted) calls directly:
   ```solidity
   pool.addLiquidity(
       bob,           // owner = allowlisted address
       salt,
       deltas,
       callbackData,  // alice pays via callback
       extensionData
   );
   ```
4. Pool calls `_beforeAddLiquidity(alice, bob, salt, deltas, extensionData)`.
5. Extension evaluates `allowedDepositor[pool][bob]` → `true` → no revert.
6. Alice's tokens are pulled via callback; the LP position is credited to `bob`.
7. Alice has deposited into the curated pool without being allowlisted. The pool admin's curation policy is violated. [3](#0-2) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L181-196)
```text
  /// @inheritdoc IMetricOmmPoolActions
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

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-20)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-38)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L194-196)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
