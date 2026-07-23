### Title
`SwapAllowlistExtension.beforeSwap` Checks the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist on Curated Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the actual user. The extension therefore checks whether the router is allowlisted, not the actual user. If the router is allowlisted (the natural configuration for any pool intended to be accessible via the supported periphery), every user in the world can bypass the per-user swap allowlist by routing through the router.

---

### Finding Description

**Call chain:**

```
user (alice) → MetricOmmSimpleRouter.exactInputSingle()
                  → pool.swap(recipient, ...) [msg.sender = router]
                      → _beforeSwap(msg.sender=router, recipient, ...)
                          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← this is the ROUTER, not alice
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to the extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`.

**Bypass path:** A pool admin who wants to allow router-mediated swaps calls `setAllowedToSwap(pool, router, true)`. This is the natural and expected configuration for any pool that is meant to be accessible via the supported periphery. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true` for every call that comes through the router — regardless of who the actual user is. Any unprivileged user can now swap on the curated pool by calling `exactInputSingle` or `exactInput` on the router.

**Secondary breakage:** Even without the router being allowlisted, the allowlist is broken for router users. A pool admin who allowlists `alice` directly (`setAllowedToSwap(pool, alice, true)`) will find that Alice cannot swap through the router (the check sees `router`, not `alice`), forcing her to call the pool directly — which requires implementing `IMetricOmmSwapCallback`, an unreasonable burden for an EOA.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The allowlist — the sole access-control mechanism on the swap path — silently fails open. Unauthorized users can execute swaps, drain LP-provided liquidity at oracle prices, and extract value from a pool that was designed to be closed to them. This is a direct loss of LP assets and a broken core pool functionality.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entrypoint for the protocol. Any pool admin who configures a swap allowlist and also wants their pool to be accessible via the router will naturally allowlist the router. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — only a call to the public `exactInputSingle` or `exactInput` function on the router.

---

### Recommendation

The extension must check the economically relevant actor — the original user — not the intermediary. The pool should pass the original caller's identity separately, or the extension should be redesigned to receive it. One approach: add a dedicated `swapper` field to the extension interface that the pool populates with the original `msg.sender` before any router indirection. Alternatively, the `SwapAllowlistExtension` should check `recipient` (the address receiving output tokens) if that is the intended gated identity, or the router should forward the original `msg.sender` via `extensionData` and the extension should decode it.

A minimal fix at the extension level would be to check `recipient` instead of `sender` when the pool is the caller, but the cleanest fix is for the pool to expose the original initiator as a distinct parameter in the hook interface.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — intending to allow router-mediated swaps.
3. Pool admin does **not** call `setAllowedToSwap(pool, alice, true)` — alice is not an authorized swapper.
4. Alice calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
5. Pool calls `_beforeSwap(router, ...)`.
6. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Alice successfully swaps on a pool she was never authorized to access.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
