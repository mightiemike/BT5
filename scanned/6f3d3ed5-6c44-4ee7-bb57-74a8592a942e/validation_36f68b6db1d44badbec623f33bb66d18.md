### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which the pool sets to `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the router is allowlisted (the natural operational setup), every user — including those not individually permitted — can bypass the per-user allowlist by routing through the public router.

---

### Finding Description

**`SwapAllowlistExtension.beforeSwap()`** performs this check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Inside the extension callback, `msg.sender` is the pool (the extension is invoked by the pool via `_callExtensionsInOrder`), and `sender` is the first argument forwarded by the pool. The pool sets that argument to its own `msg.sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← immediate caller of pool.swap(), not the end user
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

The pool's `msg.sender` is now the **router address**. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Bypass scenario:**

A pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses. To allow those users to also swap through the router, the admin must allowlist the router (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, **any** address — including those the admin explicitly never permitted — can call `exactInputSingle()` through the router and pass the allowlist check, because the extension only sees the router's address.

The same structural flaw exists in `exactInput`, `exactOutputSingle`, and `exactOutput` paths, all of which call `pool.swap()` with the router as `msg.sender`.

---

### Impact Explanation

The allowlist invariant is broken: a pool configured to restrict swaps to specific addresses can be bypassed by any unprivileged user routing through the public `MetricOmmSimpleRouter`. Depending on the pool's purpose (e.g., institutional-only liquidity, KYC-gated pools, rate-limited market makers), this allows unauthorized parties to execute swaps, drain one-sided liquidity at oracle-anchored prices, or extract value the pool admin intended to reserve for permitted counterparties. The loss is direct and bounded only by available pool liquidity.

---

### Likelihood Explanation

The bypass requires only that the router be allowlisted for the pool — a configuration any operator who wants to support router-mediated swaps must make. There is no special privilege, no malicious setup, and no non-standard token required. Any public user can call `exactInputSingle()` on the router with the target pool address.

---

### Recommendation

The extension must check the **original user**, not the immediate pool caller. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a coordinated convention between router and extension.
2. **Check `recipient` instead of `sender`**: For swap allowlists the economically relevant actor is the recipient of output tokens. The extension already receives `recipient` as its second argument (currently ignored). Gating on `recipient` is harder to spoof through a router.
3. **Dedicated router-aware allowlist**: Extend the extension to accept a trusted router list; when `sender` is a known router, decode the real user from `extensionData` and apply the per-user check.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, alice, true)   // only alice is permitted
3. Admin calls setAllowedToSwap(pool, router, true)  // router must be allowed for alice to use it
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ...) — pool's msg.sender = router
6. Pool calls extension.beforeSwap(router, recipient, ...)
7. Extension checks allowedSwapper[pool][router] → true → swap proceeds
8. Bob successfully swaps on an allowlist-gated pool without being individually permitted.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
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
