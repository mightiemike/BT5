### Title
`DepositAllowlistExtension` Guards `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` parameter and gates on `owner` instead. Because `MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` address, any unprivileged address can pass an allowlisted address as `owner`, satisfy the check, pay the tokens via callback, and register a position in a pool that was intended to be restricted.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses to the extension hook:

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
//                  ^^^^^^^^^^  ^^^^^
//                  sender      owner (caller-supplied)
``` [1](#0-0) 

The `IMetricOmmExtensions` interface names these `sender` (the actual depositor) and `owner` (the position beneficiary): [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` discards `sender` entirely (unnamed first parameter) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
  }
  ...
}
``` [3](#0-2) 

The sibling `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swapper), not the recipient: [4](#0-3) 

The inconsistency is structural: the deposit guard checks the wrong actor.

---

### Impact Explanation

Any address can deposit into a pool protected by `DepositAllowlistExtension` by supplying an allowlisted address as `owner`. The callback (`metricOmmModifyLiquidityCallback`) is invoked on `msg.sender` (the actual caller), so the caller pays the tokens: [5](#0-4) 

The position is then registered under the supplied `owner` address. Because `removeLiquidity` enforces `msg.sender == owner`, the allowlisted `owner` can later withdraw the tokens. A colluding pair (Bob pays, Alice withdraws and returns funds off-chain) fully circumvents the allowlist. Even without collusion, an attacker can force tokens into an allowlisted address's position (griefing) or simply participate in a pool that was intended to be permissioned (e.g., KYC-gated, institutional-only). This breaks the core invariant of `DepositAllowlistExtension` — that only explicitly approved addresses may deposit — and constitutes broken core pool functionality with direct fund-flow consequences.

---

### Likelihood Explanation

The bypass requires no special privileges, no flash loan, and no oracle manipulation. Any externally-owned account can call `addLiquidity` with `owner = <any allowlisted address>`. The allowlisted address is publicly readable from `allowedDepositor` storage. Likelihood is **high**.

---

### Recommendation

Check `sender` (the actual depositor) instead of `owner`, mirroring the pattern used in `SwapAllowlistExtension`:

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

If the intent is to gate on both the depositor and the position owner, both should be checked explicitly.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension`; `allowAllDepositors[pool] = false`.
2. Admin calls `setAllowedToDeposit(pool, Alice, true)`. Bob is **not** allowlisted.
3. Bob calls:
   ```solidity
   pool.addLiquidity(
     owner = Alice,   // allowlisted — passes the guard
     salt  = 0,
     deltas = <valid bins>,
     callbackData = ...,
     extensionData = ""
   );
   ```
4. `_beforeAddLiquidity(sender=Bob, owner=Alice, ...)` is dispatched to the extension.
5. Extension evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` calls `metricOmmModifyLiquidityCallback` on Bob; Bob transfers tokens.
7. Position shares are credited to `positionBinKey(Alice, 0, binIdx)`.
8. Bob has deposited into a restricted pool. Alice (or a colluding Bob+Alice pair) calls `removeLiquidity` to extract the tokens. [3](#0-2) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L147-154)
```text
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
        if (amount0Added > 0 && balance0Before + amount0Added > IERC20(ctx.token0).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
        if (amount1Added > 0 && balance1Before + amount1Added > IERC20(ctx.token1).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
```
