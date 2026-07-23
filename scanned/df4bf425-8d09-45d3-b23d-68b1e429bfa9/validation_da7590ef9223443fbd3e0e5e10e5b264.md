### Title
`DepositAllowlistExtension` checks LP position `owner` instead of actual depositor `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity()` is documented as gating `addLiquidity` by **depositor address**, but it silently discards the `sender` argument (the actual caller paying tokens) and instead checks the `owner` argument (the LP position recipient). Because `owner` is a free caller-controlled parameter in `pool.addLiquidity(address owner, ...)`, any non-allowlisted address can pass the guard by naming any allowlisted address as `owner`. The caller's tokens are still pulled via the callback and deposited into the pool; only the LP credit goes to the named `owner`.

---

### Finding Description

The pool dispatches the hook as:

```solidity
// MetricOmmPool.sol:191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`msg.sender` (the actual depositor) is the first argument; `owner` (the LP recipient, a free parameter) is the second. The extension silently drops `sender` and checks `owner`:

```solidity
// DepositAllowlistExtension.sol:32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The first parameter (`sender`) is unnamed and never read. The allowlist lookup key is `owner`, which the caller sets freely. After the guard passes, `LiquidityLib.addLiquidity` calls back on `msg.sender` (the actual caller) to pull tokens:

```solidity
// LiquidityLib.sol:147-148
IMetricOmmModifyLiquidityCallback(msg.sender)
    .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
```

So the non-allowlisted caller pays the tokens, the allowlisted `owner` receives the LP shares, and the guard never fires. The `SwapAllowlistExtension` correctly checks `sender` for its analogous guard, confirming the asymmetry is a defect, not a design choice:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

The `MetricOmmPoolLiquidityAdder` explicitly supports depositing on behalf of a different `owner` (confirmed by `test_exactShares_canAddOnBehalfOfAnotherOwner`), making the bypass trivially reachable through the production periphery router as well.

---

### Impact Explanation

The deposit allowlist is rendered completely ineffective. Any non-allowlisted address can deposit tokens into a restricted pool by supplying any allowlisted address as `owner`. The pool admin's intended access control — whether for regulatory compliance, KYC gating, or liquidity composition control — is bypassed without any special privilege. The allowlisted `owner` receives an unsolicited LP position they did not initiate; the actual depositor loses their tokens to the pool (they can be recovered only if the `owner` cooperates to remove liquidity). This constitutes a broken core pool functionality (admin-boundary break via an unprivileged path) and a broken allowlist invariant with direct fund-movement consequences.

---

### Likelihood Explanation

Exploitation requires a single direct call to `pool.addLiquidity(owner=<any_allowlisted_address>, ...)` with a valid callback implementation. No special permissions, flash loans, or multi-step setup are needed. The `MetricOmmPoolLiquidityAdder` router makes this reachable from EOAs via `addLiquidityExactShares(pool, owner, ...)` with `owner` set to any allowlisted address. The only prerequisite is knowing one allowlisted address, which is publicly readable from `allowedDepositor`.

---

### Recommendation

Check `sender` (the actual depositor, first parameter) instead of `owner` (the LP recipient, second parameter):

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap()`.

---

### Proof of Concept

1. Deploy a pool with `DepositAllowlistExtension` wired into `BEFORE_ADD_LIQUIDITY_ORDER`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is allowlisted; `bob` is not.
3. `bob` (non-allowlisted, holding tokens and implementing `IMetricOmmModifyLiquidityCallback`) calls:
   ```solidity
   pool.addLiquidity(
       alice,          // owner — allowlisted, check passes
       salt,
       deltas,
       callbackData,
       extensionData
   );
   ```
4. `beforeAddLiquidity` checks `allowedDepositor[pool][alice]` → `true` → no revert.
5. `LiquidityLib` calls `bob.metricOmmModifyLiquidityCallback(...)` — `bob` transfers tokens to the pool.
6. LP shares are credited to `alice` at key `keccak256(abi.encode(alice, salt, binIdx))`.
7. `bob` has deposited tokens into the restricted pool; the allowlist guard never triggered. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L144-155)
```text
      if (amount0Added > 0 || amount1Added > 0) {
        uint256 balance0Before = IERC20(ctx.token0).balanceOf(address(this));
        uint256 balance1Before = IERC20(ctx.token1).balanceOf(address(this));
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
        if (amount0Added > 0 && balance0Before + amount0Added > IERC20(ctx.token0).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
        if (amount1Added > 0 && balance1Before + amount1Added > IERC20(ctx.token1).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
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
