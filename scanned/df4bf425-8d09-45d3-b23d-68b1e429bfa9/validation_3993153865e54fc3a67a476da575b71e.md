### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router's address is passed as `sender`, not the actual user's address. If the pool admin allowlists the router to enable router-mediated swaps, any unprivileged user can bypass the allowlist entirely by calling the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the caller of the extension) and `sender` is the first argument forwarded from `ExtensionCalling._beforeSwap`, which is `msg.sender` of `pool.swap()`. [1](#0-0) 

In `MetricOmmPool.swap`, the pool passes `msg.sender` (its direct caller) as `sender` to the extension: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [3](#0-2) 

So `msg.sender` of `pool.swap()` = the router address. The extension then checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The same identity substitution occurs for `exactInput` (multi-hop) and `exactOutputSingle`/`exactOutput`: [4](#0-3) 

**Bypass path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to specific addresses.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for those users.
3. Any unprivileged user calls `router.exactInputSingle(pool, ...)`. The extension checks `allowedSwapper[pool][router]` → `true`. The swap proceeds regardless of whether the actual user is allowlisted.

The pool admin cannot selectively allow specific users to swap via the router while blocking others. Allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)`.

---

### Impact Explanation

LPs in a restricted pool suffer direct loss of principal. The swap allowlist is typically deployed to prevent toxic flow (e.g., restricting swaps to trusted market makers or KYC'd counterparties). Once bypassed, any user can execute swaps against the pool's oracle-anchored bins, extracting value from LPs at the oracle mid-price without the intended access control. The pool's entire LP-deposited token balance is exposed to unauthorized swappers.

---

### Likelihood Explanation

A pool admin who wants to allow router-mediated swaps for specific users will naturally allowlist the router address — this is the only mechanism available. The admin is unlikely to realize that allowlisting the router is equivalent to disabling the allowlist for all router users. The `MetricOmmSimpleRouter` is the primary user-facing swap interface, making this configuration path common.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the actual end-user identity, not the direct caller of `pool.swap()`. Two approaches:

1. **Pass actual user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension reads and verifies it. However, this requires the extension to trust the router, which must itself be verified (e.g., via a factory-registered router registry).

2. **Check `sender` against a router registry**: If `sender` is a known router, the extension should reject the call unless the pool is configured to allow all-router access, making the distinction explicit to the pool admin.

The current design creates a false sense of security: the pool admin believes they are restricting swaps to specific users, but any user can bypass the guard via the public router.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true  (only alice intended)
  - allowedSwapper[pool][router] = true (admin enables router for alice)

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(recipient, ...) → msg.sender = router
  - pool calls extension.beforeSwap(router, ...) → sender = router
  - extension checks allowedSwapper[pool][router] → true → PASSES
  - bob's swap executes against LP funds, allowlist bypassed
``` [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
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
