### Title
`SwapAllowlistExtension` checks router address instead of actual user, enabling complete allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

The `SwapAllowlistExtension.beforeSwap` hook gates swap access by checking the `sender` parameter, which is the **direct caller of `pool.swap()`**. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps, any user can bypass the allowlist entirely by going through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded from `ExtensionCalling._beforeSwap`, which is `msg.sender` of the pool's `swap()` call. [1](#0-0) 

In `MetricOmmPool.swap`, the value forwarded as `sender` is `msg.sender` of the pool call: [2](#0-1) 

`ExtensionCalling._beforeSwap` encodes that value verbatim: [3](#0-2) 

When a user swaps through `MetricOmmSimpleRouter.exactInputSingle`, the call chain is:

```
user → router.exactInputSingle(params)   [msg.sender = user]
     → pool.swap(recipient, ...)          [msg.sender = router]
     → _beforeSwap(sender = router, ...)
     → SwapAllowlistExtension.beforeSwap(sender = router)
     → checks allowedSwapper[pool][router]
``` [4](#0-3) 

The router never forwards the original `msg.sender` (the actual user) to the pool. The pool only sees the router as `msg.sender`. Therefore the allowlist always checks the router address, not the actual user.

This creates two mutually exclusive failure modes:

| Pool admin action | Result |
|---|---|
| Allowlists the router (to enable router swaps) | **Any user bypasses the allowlist** through the router |
| Does not allowlist the router | **Allowlisted users cannot use the router** at all |

There is no configuration that simultaneously allows specific users through the router while blocking others.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` parameter (the LP position owner), which is explicitly passed by the caller and represents the economically relevant actor. The swap extension has no equivalent mechanism — the actual user's address is never available to the pool or extension. [5](#0-4) 

---

### Impact Explanation

**HIGH.** When the pool admin allowlists the router (the natural action to enable the standard periphery interface), the `SwapAllowlistExtension` is completely neutralized. Any unprivileged user can trade on a curated pool by routing through `MetricOmmSimpleRouter`, bypassing the access control the pool admin configured. This is a direct admin-boundary break: an unprivileged path (`router → pool.swap`) causes the allowlist guard to pass for actors it was never intended to authorize.

---

### Likelihood Explanation

**HIGH.** The router is the standard, documented periphery entry point for swaps. A pool admin who deploys a curated pool with `SwapAllowlistExtension` and wants to support the standard UI/router workflow will allowlist the router. The bypass is then unconditional and requires no special timing, state manipulation, or privileged access — any user simply calls `router.exactInputSingle`.

---

### Recommendation

The `SwapAllowlistExtension` must gate the economically relevant actor, not the intermediary. Two viable approaches:

1. **Pass the original user via `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`. The extension decodes and verifies it. This requires a trusted router assumption or a signature scheme.

2. **Check `sender` against the router and fall back to a per-router user registry**: The extension recognizes known router addresses and requires the router to attest the real user in `extensionData`.

3. **Gate by `recipient` instead of `sender`**: If the pool's design intent is to restrict who *receives* output tokens, `recipient` (already passed to the extension) is router-independent. This changes the semantics but may match the actual curation goal.

The simplest safe fix is option 1: require the router to ABI-encode the original `msg.sender` into `extensionData`, and have the extension decode and check that address when `sender` is a known router.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls extension.setAllowedToSwap(pool, router, true)
    // admin intends: "allow router-mediated swaps for my allowlisted users"
    // actual effect: any user is now allowed

Attack:
  attacker (not in allowedSwapper[pool]) calls:
    router.exactInputSingle({pool: pool, ...})

  Execution:
    router → pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
      pool._beforeSwap(sender=router, ...)
        SwapAllowlistExtension.beforeSwap(sender=router)
          allowedSwapper[pool][router] == true  ✓
          → no revert
      swap executes successfully

Result:
  Attacker swaps on a curated pool that was supposed to block them.
  The SwapAllowlistExtension is completely bypassed.
``` [6](#0-5) [7](#0-6) [2](#0-1)

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
