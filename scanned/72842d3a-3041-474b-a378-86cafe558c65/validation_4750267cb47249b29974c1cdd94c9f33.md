### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any user to bypass the per-pool allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, that `sender` is the router contract, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for authorized users inadvertently opens the gate to every unprivileged caller.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with its own `msg.sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` checks that `sender` against the allowlist.**

```solidity
function beforeSwap(address sender, address, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`.

**Step 3 — `MetricOmmSimpleRouter` calls `pool.swap()` as itself.**

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The pool's `msg.sender` is the **router**, so the extension receives `sender = router`. The actual end user (`msg.sender` of the router call) is never visible to the extension.

**The structural trap:** A pool admin who wants allowlisted users to be able to use the router must add the router to `allowedSwapper[pool]`. The moment they do, `allowedSwapper[pool][router] == true` for every call that arrives through the router — regardless of who the real caller is. Any unprivileged user can then call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` and the allowlist check passes.

The same wrong-actor binding applies to the multi-hop `exactInput` path (intermediate hops use `address(this)` as payer but still call `pool.swap()` from the router) and the recursive `exactOutput` path. [5](#0-4) 

---

### Impact Explanation

A curated pool's entire swap-allowlist policy is silently voided. Any unprivileged address can trade on the pool by routing through `MetricOmmSimpleRouter`. LP providers who deposited under the assumption that only vetted counterparties would trade against them are exposed to unrestricted order flow, which can drain the pool at oracle-quoted prices or trigger stop-loss extensions prematurely. This is a direct, fund-impacting policy bypass reachable by any public caller.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical periphery swap entry point. Pool admins who deploy `SwapAllowlistExtension` and want their allowlisted users to have a normal UX will naturally allowlist the router. The bypass requires no special knowledge — any user who notices the router is allowlisted can exploit it immediately. The only scenario where the bypass does not apply is if the pool admin deliberately blocks the router entirely, which also breaks the UX for legitimate users.

---

### Recommendation

**Short term:** Do not rely on `sender` (the direct pool caller) to identify the economic actor. Instead, require the actual user address to be passed in `extensionData` and have the extension verify it matches a signed or trusted source. Alternatively, document explicitly that `SwapAllowlistExtension` is incompatible with any shared router and must only be used with direct `pool.swap()` calls.

**Long term:** Redesign the extension interface so the pool propagates the original transaction originator (`tx.origin` is unsafe; a signed permit or a trusted forwarder pattern is preferable) alongside `sender`, giving allowlist extensions a reliable identity to gate on regardless of routing depth.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is intended to trade.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary so `userA` can use the router.
4. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
6. Pool calls `_beforeSwap(router, ...)` → extension evaluates `allowedSwapper[pool][router]` → **true**.
7. `userB`'s swap executes successfully; the allowlist is completely bypassed. [6](#0-5) [1](#0-0) [7](#0-6)

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
