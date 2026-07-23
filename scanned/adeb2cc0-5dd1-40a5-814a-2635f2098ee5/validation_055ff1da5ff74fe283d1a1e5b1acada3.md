Based on my investigation of the repository, I found a genuine analog to the external bug class: a configured guard that is misapplied with fund-impacting consequences.

---

### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Permanently Breaking Per-User Allowlist Enforcement - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When users route through `MetricOmmSimpleRouter`, `sender` resolves to the **router contract address**, not the actual end user. This makes it impossible to simultaneously allow router-mediated swaps and restrict access to specific users: either the router is allowlisted (opening the pool to all users) or it is not (blocking all router users, including allowlisted ones).

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards the pool's `msg.sender` as `sender`:

```solidity
// metric-core/contracts/ExtensionCalling.sol
function _beforeSwap(address sender, address recipient, ...) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
    );
}
``` [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any router entry point) calls `pool.swap(...)`, the pool's `msg.sender` is the **router contract**, not the end user:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

So `allowedSwapper[pool][sender]` resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` parameter (the position owner), which the liquidity adder always sets to the actual user — making the deposit allowlist work correctly while the swap allowlist does not: [4](#0-3) 

### Impact Explanation

Two mutually exclusive failure modes arise for any pool using `SwapAllowlistExtension`:

1. **Allowlist bypass**: If the pool admin allowlists the router address (a natural step to let their approved users trade via the router), every unpermissioned user can also swap through the router — the per-user restriction is completely defeated.
2. **Broken core functionality**: If the pool admin does not allowlist the router, all router-mediated swaps revert for every user, including those individually allowlisted — the supported periphery path is unusable.

There is no configuration that simultaneously allows router usage and restricts access to specific users. A curated pool (e.g., one restricted to specific market makers) is either fully open to all router users or fully closed to router users.

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and expects users to interact via `MetricOmmSimpleRouter` hits this immediately. The pool admin allowlisting the router is the natural and expected action to enable router support, making the bypass scenario the default outcome rather than an edge case.

### Recommendation

Pass the **original end-user address** through the swap call so the extension can check it. One approach: have the pool record `tx.origin` or have the router pass the user address in `extensionData`, and have `SwapAllowlistExtension` decode it. A cleaner fix is to add a `swapper` field to the swap parameters that the router populates with `msg.sender` before calling the pool, and have the pool forward it as a distinct argument to the extension (separate from `sender`).

Alternatively, mirror the deposit allowlist pattern: gate on the **recipient** (the economic beneficiary of the swap) rather than the `sender` (the contract that called the pool).

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps for their approved users.
3. Unpermissioned user `Charlie` (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
4. The pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
5. Charlie successfully swaps in a pool that was supposed to restrict him, because the router address — not Charlie's address — is what the allowlist checks.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
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
