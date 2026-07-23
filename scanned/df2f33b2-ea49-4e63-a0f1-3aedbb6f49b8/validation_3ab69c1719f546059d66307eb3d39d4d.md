### Title
SwapAllowlistExtension Gates the Router Address Instead of the End-User, Allowing Allowlist Bypass on Curated Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the value the pool forwards from its own `msg.sender` (the router). When a user enters through `MetricOmmSimpleRouter`, the extension sees the router's address as the swapper identity, not the end user's address. A pool admin who intends to gate individual users can be bypassed by any user routing through the public router, or conversely, allowlisted users are blocked from using the router entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
``` [2](#0-1) 

The pool's `swap` function populates `sender` from its own `msg.sender`. When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract, so the extension receives `sender = router`. The extension then evaluates `allowedSwapper[pool][router]` — the router's allowlist status — rather than the end user's.

The `IMetricOmmPoolActions` interface confirms this: the error is described as "Swap allowlist rejected `msg.sender`", meaning the pool's `msg.sender` (the router) is the checked identity. [3](#0-2) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores the first `sender` parameter and checks the explicit `owner` argument, which the pool takes as a named parameter and which the liquidity adder sets to the actual position owner:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
``` [4](#0-3) 

The swap path has no equivalent explicit user-identity parameter; the pool only forwards its own `msg.sender`.

---

### Impact Explanation

**Bypass path (High):** If the pool admin allowlists the router address (a natural operational step so that normal users can trade), every non-allowlisted address can bypass the curated gate by routing through `MetricOmmSimpleRouter`. The allowlist provides zero protection against unauthorized swappers on the pool.

**Lockout path (Medium):** If the pool admin does not allowlist the router, every allowlisted user is blocked from using the router. They must call the pool directly, breaking the standard UX and making the extension incompatible with the supported periphery.

Both outcomes break the core invariant that a curated pool enforces the same allowlist policy regardless of which supported public entrypoint reaches it. [5](#0-4) 

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public swap entrypoint. Any user who calls `exactInput` or `exactOutput` through the router triggers this path. No special permissions, flash loans, or privileged access are required. The bypass is reachable on every router-mediated swap against a pool that has `SwapAllowlistExtension` configured in its `beforeSwap` order. [6](#0-5) 

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should check the `recipient` or an explicit user-supplied identity rather than the `sender` forwarded by the pool's `msg.sender`. The cleanest fix mirrors the deposit extension pattern: the pool's `swap` function should accept an explicit `swapper` parameter (analogous to `owner` in `addLiquidity`) that the router populates with the actual end user's address, and the extension should gate on that value. Alternatively, the extension can decode the true swapper from `extensionData` when the router is the caller. [7](#0-6) 

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` (allowlisting the router so normal users can trade).
3. A non-allowlisted address `attacker` calls `router.exactInput(...)` targeting the pool.
4. The router calls `pool.swap(...)`. The pool's `msg.sender` = router.
5. `ExtensionCalling._beforeSwap` forwards `sender = router` to the extension.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `attacker` successfully trades on a pool that was supposed to block them.

Conversely, if step 2 is skipped (router not allowlisted), an allowlisted user calling through the router hits `allowedSwapper[pool][router]` → `false` → `NotAllowedToSwap`, even though their own address is allowlisted. [1](#0-0) [2](#0-1)

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L111-113)
```text
  /// @notice Swap allowlist rejected `msg.sender`.
  /// @dev Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set; `simulateSwapAndRevert` does not, so a passing simulation does not imply an allowed live swap.
  error NotAllowedToSwap();
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
