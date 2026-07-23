### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Unauthorized Depositors to Bypass the Allowlist Guard - (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

The `DepositAllowlistExtension` is titled "Gates `addLiquidity` by depositor address" and uses a mapping named `allowedDepositor`. However, its `beforeAddLiquidity` hook silently discards the `sender` parameter (the actual caller who pays tokens) and only checks `owner` (the position owner). Because `addLiquidity` explicitly supports an operator pattern where `msg.sender != owner`, any unprivileged address can bypass the allowlist by calling `addLiquidity(owner = allowlisted_address)`. The sibling `SwapAllowlistExtension` correctly checks `sender` in `beforeSwap`, confirming the intended pattern and making this an inconsistency with fund-impacting consequences.

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct actor addresses to every extension hook:

- `sender` = `msg.sender` of the pool call — the address that **pays tokens** via the modify-liquidity callback.
- `owner` = the caller-supplied argument — the address that **receives the position** (keyed as `(owner, salt)` in pool storage). [1](#0-0) 

The pool forwards both to `_beforeAddLiquidity`: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but **silently discards it** (unnamed `address`). It only checks `owner`: [3](#0-2) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly names and checks `sender`: [4](#0-3) 

Because `removeLiquidity` enforces `msg.sender == owner`, the position is permanently attributed to `owner`, not `sender`. The economic action (token payment) is attributed to `sender`, but the guard keys on `owner`. This is the exact wrong-actor binding described in the audit's own scan vectors. [5](#0-4) 

### Impact Explanation

An unprivileged address (Bob, not on the allowlist) can call `pool.addLiquidity(owner = Alice, ...)` where Alice is allowlisted. The extension sees `owner = Alice` (passes), Bob pays tokens via callback, and Alice receives the position. The deposit allowlist — an admin-configured curation boundary — is fully bypassed for the actual payer. This constitutes an admin-boundary break: an unprivileged path circumvents a pool admin's access control. Additionally, Alice receives an unsolicited position she did not initiate, which she must actively remove (griefing with gas cost). For pools using the allowlist for regulatory compliance or curated LP access, the bypass undermines the entire security model of the extension.

### Likelihood Explanation

Medium. The `addLiquidity` function is public and the operator pattern (`sender != owner`) is an explicitly supported and documented flow. Any address that implements `IMetricOmmModifyLiquidityCallback` (trivial to write) can exploit this. No special privileges, flash loans, or oracle manipulation are required — only knowledge of one allowlisted address, which is observable on-chain from past `LiquidityAdded` events. [6](#0-5) 

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual payer) instead of `owner`, consistent with the contract's stated purpose ("Gates `addLiquidity` by depositor address"), the `allowedDepositor` mapping name, and the `SwapAllowlistExtension` pattern:

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
```

If the intent is genuinely to gate by `owner` (position recipient), the NatSpec, contract title, mapping name, and `isAllowedToDeposit` parameter name should all be updated to reflect that, and the operator-pattern bypass should be explicitly acknowledged.

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` as the `beforeAddLiquidity` hook.
2. Pool admin calls `setAllowedToDeposit(pool, Alice, true)` — only Alice is allowlisted.
3. Bob (not allowlisted) deploys a minimal contract implementing `IMetricOmmModifyLiquidityCallback` that pays tokens from Bob's balance.
4. Bob calls `pool.addLiquidity(owner = Alice, salt = 0, deltas = ..., callbackData = ..., extensionData = "")`.
5. The pool calls `_beforeAddLiquidity(sender = Bob, owner = Alice, ...)`.
6. `DepositAllowlistExtension.beforeAddLiquidity` checks `allowedDepositor[pool][Alice]` → `true` → passes.
7. The pool mints shares into position `(Alice, 0)` and calls Bob's callback to pull tokens from Bob.
8. Bob has deposited tokens into the pool despite not being on the allowlist. Alice now holds an unsolicited position.
9. Confirmed: if Bob had called `addLiquidity(owner = Bob, ...)`, the check `allowedDepositor[pool][Bob]` → `false` would revert — the bypass only works via the `owner` substitution. [3](#0-2) [7](#0-6)

### Citations

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-148)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
```
