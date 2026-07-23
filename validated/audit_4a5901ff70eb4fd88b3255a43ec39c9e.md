### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing non-allowlisted callers to bypass the deposit guard — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension` is configured to gate `addLiquidity` by depositor address. However, its `beforeAddLiquidity` hook silently discards the `sender` parameter (the actual caller) and only checks `owner` (the position owner). Because `addLiquidity` allows `owner != msg.sender`, any non-allowlisted address can bypass the deposit restriction by supplying an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` that may differ from `msg.sender`: [1](#0-0) 

The pool then calls `_beforeAddLiquidity(msg.sender, owner, ...)`, forwarding both the actual caller (`sender`) and the position owner (`owner`) to every configured extension: [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is unnamed and therefore silently ignored. Only `owner` is checked against the allowlist: [3](#0-2) 

Because `allowedDepositor[pool][owner]` is evaluated against the *position owner* rather than the *caller*, a non-allowlisted address can call `addLiquidity(owner = allowlisted_address, ...)`, pass the guard, provide tokens via the modify-liquidity callback, and create a position for the allowlisted address — effectively depositing into a restricted pool without authorization.

This is structurally inconsistent with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the caller) and ignores the recipient: [4](#0-3) 

Note also that `removeLiquidity` enforces `msg.sender == owner`, so the owner/sender split only exists on the deposit path — making the allowlist check on `owner` uniquely exploitable there: [5](#0-4) 

---

### Impact Explanation

The pool admin's deposit allowlist is completely bypassed. Any address can deposit into a pool that is supposed to be restricted to specific depositors. Pools using this extension for KYC/AML compliance, institutional access control, or permissioned liquidity programs are rendered unprotected. This is an admin-boundary break: the pool admin configured an access-control guard, but an unprivileged path circumvents it without any special role or privilege.

---

### Likelihood Explanation

Medium. The attacker needs only to know one allowlisted address (readable from the public `allowedDepositor` mapping) and call `addLiquidity` with that address as `owner`. No special privileges are required. The attacker loses the deposited tokens (they go into the allowlisted address's position), so the motivation is regulatory evasion or griefing rather than direct profit — but the guard is fully defeated regardless.

---

### Recommendation

Check `sender` (the actual caller) instead of `owner`, consistent with `SwapAllowlistExtension`:

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address sender, address /*owner*/, uint80, LiquidityDelta calldata, bytes calldata)
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

If the intent is to gate both the caller *and* the position owner, check both addresses.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` configured as a `beforeAddLiquidity` hook.
2. Pool admin allowlists Alice: `setAllowedToDeposit(pool, Alice, true)`.
3. Bob (not allowlisted) calls `pool.addLiquidity(owner = Alice, salt = 0, deltas = ..., callbackData = ..., extensionData = "")`.
4. Pool calls `DepositAllowlistExtension.beforeAddLiquidity(sender = Bob, owner = Alice, ...)`.
5. Extension evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
6. Bob's `metricOmmModifyLiquidityCallback` provides the required tokens.
7. Position shares are minted for Alice; Bob has deposited into the restricted pool without being allowlisted. [3](#0-2) [1](#0-0)

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
