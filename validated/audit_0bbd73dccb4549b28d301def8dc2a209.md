### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the actual user. A pool admin who allowlists the router address (the natural step to enable router-mediated swaps for their curated users) inadvertently opens the pool to every user who routes through the same router, completely defeating the allowlist.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol – swap()
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` encodes that value as the first argument to `IMetricOmmExtensions.beforeSwap`:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)   // sender = msg.sender of pool.swap()
)
```

`SwapAllowlistExtension.beforeSwap` then gates on that value:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

The router is `msg.sender` to the pool, so the extension receives `sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Bypass path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses.
2. Admin allowlists specific users: `setAllowedToSwap(pool, userA, true)`.
3. Admin also allowlists the router so that allowlisted users can swap through the standard periphery: `setAllowedToSwap(pool, router, true)`.
4. Any non-allowlisted user B calls `router.exactInputSingle(pool, ...)`. The extension sees `sender = router`, finds `allowedSwapper[pool][router] = true`, and passes. User B's swap executes against the curated pool.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` with `msg.sender = router`.

---

### Impact Explanation

A curated pool whose admin has allowlisted the router loses its swap restriction entirely. Any unprivileged user can execute swaps against the pool's LP liquidity by routing through `MetricOmmSimpleRouter`. LP providers who deposited under the assumption that only vetted counterparties could trade against their positions are exposed to unrestricted adverse selection, fee extraction, and potential oracle-anchored price manipulation by arbitrary actors. This is a direct loss of LP principal and owed fees above Sherlock thresholds.

---

### Likelihood Explanation

Likelihood is high. Allowlisting the router is the natural and expected administrative action for any pool that wants its curated users to access the standard periphery. The admin has no on-chain signal that doing so opens the pool to everyone. The bypass requires no special privilege, no malicious setup, and no non-standard token — any user with a standard ERC-20 balance can trigger it in a single transaction.

---

### Recommendation

The extension must gate on the economically relevant actor, not the intermediary. Two options:

1. **Check `sender` only for direct pool calls; require the router to forward the real user identity** — add a `recipient`-or-caller field to the swap path that the router populates with `msg.sender` and that the extension reads from `extensionData`.

2. **Gate on `recipient` instead of `sender`** — if the pool's curation intent is "only allowlisted addresses may receive swap output", checking `recipient` (the second argument to `beforeSwap`) is already correctly forwarded by the router as `params.recipient`, which the user controls and which the admin can allowlist directly.

3. **Reject router-mediated swaps at the extension level** — if the pool is curated, the extension can check that `sender == tx.origin` (direct EOA only) or maintain a separate registry of trusted forwarders that must themselves enforce per-user checks.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin: setAllowedToSwap(pool, userA, true)
  admin: setAllowedToSwap(pool, router, true)   ← enables router for userA

Attack:
  userB (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: userB, ...})

  router calls pool.swap(recipient=userB, ...)
    msg.sender to pool = router

  pool calls extension.beforeSwap(sender=router, ...)
    extension checks: allowedSwapper[pool][router] == true  → passes

  userB's swap executes; LP funds are consumed by a non-allowlisted actor.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
