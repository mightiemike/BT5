### Title
`SwapAllowlistExtension` checks the router address as `sender` instead of the end-user, allowing any unprivileged caller to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so `sender` becomes the router address. If the pool admin allowlists the router (the natural step to let allowlisted users use the standard periphery), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// ExtensionCalling.sol:160-176
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks the allowlist keyed on this `sender`:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`), the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

At this point `msg.sender` of `pool.swap()` is the router contract, so `sender = router` reaches the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The same substitution occurs in every router path:
- `exactInput` multi-hop: router calls each `pool.swap()` directly.
- `exactOutput` multi-hop: intermediate swaps are issued from `_exactOutputIterateCallback`, which executes inside the router, so `msg.sender` of each `pool.swap()` is still the router.

**The inescapable trap for the pool admin:** to let allowlisted users use the standard periphery, the admin must call `setAllowedToSwap(pool, router, true)`. The moment the router is allowlisted, every user — allowlisted or not — can bypass the gate by routing through `MetricOmmSimpleRouter`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) is fully open to any public user the instant the pool admin allowlists the router. The attacker receives pool output tokens and the pool receives input tokens, so LP principal is directly at risk from trades the pool was designed to reject. This matches the "allowlist bypass" impact class: broken core pool functionality and direct loss of LP assets above Sherlock thresholds.

---

### Likelihood Explanation

Likelihood is **High**. Allowlisting the router is the expected operational step for any pool admin who wants allowlisted users to interact via the standard periphery rather than calling the pool directly. The bypass requires no special privileges, no flash loans, and no multi-transaction setup — a single `exactInputSingle` call from any EOA suffices. The router is a public, immutable, permissionless contract.

---

### Recommendation

The extension must verify the original end-user, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Pass the original initiator through the router.** `MetricOmmSimpleRouter` should store `msg.sender` in transient storage before calling `pool.swap()` and expose it via a callback or encode it in `extensionData`. The extension can then decode and check the real initiator.

2. **Alternatively, gate on `sender` only when `sender` is not a known router.** The extension could maintain a registry of trusted routers and, when `sender` is a router, require the router to attest the real user in `extensionData`.

The simplest safe fix is option 1: encode the real user in `extensionData` inside the router and have the extension decode and verify it, so the allowlist always gates the economically relevant actor regardless of which periphery path is used.

---

### Proof of Concept

```
Setup:
  pool admin deploys pool with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)       // alice is the intended grantee
  pool admin calls setAllowedToSwap(pool, router, true)      // to let alice use the router

Attack (executed by bob, who is NOT allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool:        pool,
      recipient:   bob,
      zeroForOne:  true,
      amountIn:    X,
      ...
  })

Trace:
  router.exactInputSingle()
    → pool.swap(recipient=bob, ...) [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, bob receives output tokens

Result:
  bob successfully swaps despite never being allowlisted.
  The allowlist check passed because it saw the router address, not bob.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
