### Title
`SwapAllowlistExtension` checks the router address instead of the originating user, enabling allowlist bypass through `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument against the per-pool allowlist. The pool passes `msg.sender` of the `swap()` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously opens the allowlist to every unprivileged user who routes through the same router.

---

### Finding Description

**Exact call chain:**

`MetricOmmPool.swap()` captures `msg.sender` and forwards it as the `sender` argument to `_beforeSwap()`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    zeroForOne,
    ...
);
```

`ExtensionCalling._beforeSwap()` encodes that value and passes it to every configured extension:

```solidity
// ExtensionCalling.sol line 162-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
```

`SwapAllowlistExtension.beforeSwap()` then checks `sender` against the allowlist keyed by `msg.sender` (the pool):

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
);
```

The pool's `msg.sender` is now the **router address**, not the originating user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same substitution occurs in `exactInput` (multi-hop, line 104), `exactOutputSingle` (line 136), and `exactOutput` (line 165 and the recursive callback at line 220).

**The two broken states this creates:**

| Admin intent | Admin action | Outcome |
|---|---|---|
| Allow only Alice to swap | `setAllowedToSwap(pool, alice, true)` | Alice cannot use the router (router not allowlisted → revert) |
| Allow Alice to use the router | `setAllowedToSwap(pool, alice, true)` + `setAllowedToSwap(pool, router, true)` | Every user can bypass the allowlist via the router |

The second state is the direct bypass: once the router is allowlisted (a natural operational step to make the extension work with the supported periphery), the allowlist is completely defeated for all users.

---

### Impact Explanation

**Direct loss / broken core functionality — High.**

A pool deploying `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, whitelisted market makers, or protocol-controlled accounts) loses that restriction entirely once the router is allowlisted. Any unprivileged user can execute swaps on the restricted pool by routing through `MetricOmmSimpleRouter`, receiving tokens at the oracle-quoted price that the pool admin intended to reserve for specific parties. LP funds are exposed to unrestricted trading against the oracle, defeating the entire purpose of the curation guard.

---

### Likelihood Explanation

**Medium.**

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool operator who deploys `SwapAllowlistExtension` and wants legitimate allowlisted users to be able to use the router will inevitably allowlist the router address, triggering the bypass. The router is a supported, documented periphery contract; using it is the expected path for most users.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **economic actor**, not the intermediary. Two complementary fixes:

1. **Pass the originating user through the pool.** Add an optional `payer` or `originator` field to the `swap()` call (or to `extensionData`) so the pool can forward the true user identity to extensions. The router already tracks the originating payer in transient storage (`_getPayer()`); it can encode that address into `extensionData` for the extension to read.

2. **Alternatively, check `recipient` instead of `sender`.** For single-hop swaps the recipient is often the user; however this is not reliable for multi-hop paths where intermediate recipients are the router itself.

The cleanest fix is option 1: the router encodes `msg.sender` into `extensionData`, and `SwapAllowlistExtension.beforeSwap()` decodes and checks that value when present, falling back to `sender` for direct pool calls.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin: setAllowedToSwap(pool, alice, true)
  pool admin: setAllowedToSwap(pool, router, true)   ← required for Alice to use the router

Attack (Bob, not allowlisted):
  Bob calls router.exactInputSingle({
      pool:      pool,
      recipient: bob,
      zeroForOne: true,
      amountIn:  X,
      ...
  })

  router calls pool.swap(bob, true, X, ...)
    → pool.msg.sender = router
    → _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  ← passes!
    → swap executes, Bob receives tokens

Result: Bob, who is not in the allowlist, successfully swaps on a curated pool.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
