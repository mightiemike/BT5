### Title
`DepositAllowlistExtension` gates on `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual caller who pays tokens) and checks `owner` (the position beneficiary) instead. Because `MetricOmmPool.addLiquidity` supports an operator pattern where `msg.sender ≠ owner`, any address not on the allowlist can deposit into a curated pool by passing an allowed address as `owner`.

---

### Finding Description

The pool's `addLiquidity` explicitly supports the operator pattern: [1](#0-0) 

`msg.sender` (the actual caller/payer) is passed as `sender`, and the caller-supplied `owner` parameter is passed as `owner`. The pool's own NatSpec documents this: *"`msg.sender` pays but need not equal `owner` (operator pattern)."* [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` forwards both actors faithfully to the extension: [3](#0-2) 

However, `DepositAllowlistExtension.beforeAddLiquidity` discards `sender` (unnamed first parameter) and checks only `owner`: [4](#0-3) 

This is the wrong-actor binding: the guard is applied to the position beneficiary, not to the address that actually pays tokens and initiates the deposit.

The inconsistency is confirmed by comparing with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual swapper): [5](#0-4) 

The NatSpec for `DepositAllowlistExtension` itself states *"Gates `addLiquidity` by depositor address"* — the depositor is the one who pays tokens (`sender`), not the position owner. [6](#0-5) 

---

### Impact Explanation

The deposit allowlist is an admin-configured guard meant to control who can provide liquidity to a curated pool. By checking `owner` instead of `sender`, any unprivileged address can bypass this guard by setting `owner` to any address already on the allowlist. The unauthorized caller pays tokens via the `IMetricOmmModifyLiquidityCallback` callback, and the position is attributed to the allowed `owner`. The pool admin's curation boundary is broken: the allowlist no longer controls who actually deposits tokens into the pool. This is an admin-boundary break — an unprivileged path bypasses a pool-admin-configured access control.

---

### Likelihood Explanation

The operator pattern (`msg.sender ≠ owner`) is explicitly supported and documented. Any user who knows a single allowed address can exploit this immediately with no special privileges, no oracle manipulation, and no complex setup. The allowed address is typically discoverable on-chain from past `setAllowedToDeposit` events.

---

### Recommendation

Check `sender` (the actual depositor/caller) instead of `owner`, consistent with `SwapAllowlistExtension` and the NatSpec intent:

```solidity
// DepositAllowlistExtension.sol
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
```

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` on `beforeAddLiquidity`.
2. Pool admin calls `setAllowedToDeposit(pool, allowedOwner, true)`.
3. Unauthorized user (not on allowlist) calls `pool.addLiquidity(allowedOwner, salt, deltas, callbackData, extensionData)`.
4. Pool calls `extension.beforeAddLiquidity(unauthorizedUser, allowedOwner, ...)`.
5. Extension evaluates `allowedDepositor[pool][allowedOwner]` → `true` → no revert.
6. Unauthorized user pays tokens via callback; position is attributed to `allowedOwner`.
7. The deposit allowlist is bypassed: the unauthorized user has deposited into the curated pool. [4](#0-3) [7](#0-6) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L188-195)
```text
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-150)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
  /// @param salt Namespace byte width for the key (`uint80`).
  /// @param deltas Parallel `binIdxs` / `shares` arrays (see `LiquidityDelta`).
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-12)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
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
  }
```
