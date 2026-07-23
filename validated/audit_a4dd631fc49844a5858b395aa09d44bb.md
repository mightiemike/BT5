### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is intended to gate `addLiquidity` by the depositor's address. Its `beforeAddLiquidity` hook silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and instead checks `owner` (the LP-position recipient). Any unprivileged address can bypass the allowlist by calling `pool.addLiquidity` with an allowlisted address as `owner`, while the unauthorized caller pays the tokens and the extension passes.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as the LP-position recipient to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

The extension hook signature is `beforeAddLiquidity(address sender, address owner, ...)`. `DepositAllowlistExtension` silently drops `sender` (unnamed first parameter) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual caller) and discards the second parameter (`recipient`):

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

The inconsistency is structural: the deposit guard checks the wrong identity field.

---

### Impact Explanation

A pool admin deploys a pool with `DepositAllowlistExtension` to restrict deposits to KYC'd or otherwise approved addresses. An unauthorized address (`attacker`) bypasses the guard by calling:

```solidity
pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData);
```

- `sender` = `attacker` (not allowlisted — ignored by the extension)
- `owner` = `allowlistedAddress` (allowlisted — passes the check)

The extension passes. The attacker pays tokens via the `metricOmmModifyLiquidityCallback` and the LP position is minted to `allowlistedAddress`. The pool admin's configured access boundary is broken: an unprivileged address has interacted with a restricted pool. The attacker can also use `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, allowlistedAddress, ...)`, which only validates `owner != address(0)`: [4](#0-3) 

This is confirmed by the existing test `test_exactShares_canAddOnBehalfOfAnotherOwner`, which shows any caller can set an arbitrary `owner`: [5](#0-4) 

**Impact**: Admin-boundary break — the pool admin's configured deposit allowlist is bypassed by an unprivileged path. Unauthorized addresses can deposit into restricted pools, violating the access control invariant. Additionally, the allowlisted `owner` receives LP shares they did not request, which can be used for griefing.

---

### Likelihood Explanation

High. The bypass requires no special privileges, no flash loan, and no complex setup. Any address can call `pool.addLiquidity` directly with an allowlisted `owner`. The `MetricOmmPoolLiquidityAdder` also exposes this path publicly. The only cost to the attacker is the token payment for the deposit.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual caller/payer) instead of `owner`, consistent with how `SwapAllowlistExtension` handles `beforeSwap`:

```diff
- function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+ function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
      external view override returns (bytes4)
  {
-     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
          revert IMetricOmmPoolActions.NotAllowedToDeposit();
      }
      return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
``` [2](#0-1) 

---

### Proof of Concept

```solidity
// Pool configured with DepositAllowlistExtension
// Only `alice` is allowlisted as a depositor

address attacker = makeAddr("attacker");
// attacker is NOT allowlisted

// Attacker calls addLiquidity with alice as owner
vm.prank(attacker);
pool.addLiquidity(
    alice,       // owner = allowlisted address → extension passes
    salt,
    deltas,
    callbackData,
    extensionData
);
// Extension checks allowedDepositor[pool][alice] → true → no revert
// Attacker pays tokens via callback
// LP position minted to alice
// Deposit allowlist bypassed
```

The extension's `beforeAddLiquidity` receives `sender = attacker` (discarded) and `owner = alice` (checked). Since `alice` is allowlisted, the guard passes and the unauthorized deposit proceeds. [2](#0-1) [6](#0-5)

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

**File:** metric-periphery/test/MetricOmmPoolLiquidityAdder.t.sol (L211-220)
```text
  function test_exactShares_canAddOnBehalfOfAnotherOwner() public {
    LiquidityDelta memory d = _deltaAbovePrice(4, 10_000);
    address bob = makeAddr("bob");

    vm.prank(alice);
    helper.addLiquidityExactShares(address(pool), bob, 1, d, type(uint256).max, type(uint256).max, "");

    uint256 bobShares = stateView.positionBinShares(address(pool), bob, 1, int8(4));
    assertGt(bobShares, 0);
  }
```
