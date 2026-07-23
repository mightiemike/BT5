### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender`. When users swap through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual end user. If the pool admin allowlists the router (required for legitimate users to use it), any unprivileged user can bypass the allowlist by routing through the same router contract.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol L37-38
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's own `swap` call.

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender` to the extension:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)` with `msg.sender = router`:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

The pool therefore calls `extension.beforeSwap(router, ...)`. The extension checks `allowedSwapper[pool][router]`.

**The invariant break:** For allowlisted users to swap through the router, the admin must add the router to the allowlist (`setAllowedToSwap(pool, router, true)`). Once the router is allowlisted, **any** user — including non-allowlisted ones — can call `router.exactInputSingle(pool, ...)` and the extension will see `sender = router` (allowlisted) and pass. The actual end user is never checked.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` on the router, since all of them call `pool.swap(...)` with `msg.sender = router`.

---

### Impact Explanation

**Critical/High.** A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses has its entire access control defeated. Any unprivileged user can trade on the restricted pool by routing through `MetricOmmSimpleRouter`. This allows unauthorized parties to extract value from pools designed for permissioned participants (e.g., KYC-gated pools, institutional pools, or pools with specific counterparty restrictions).

---

### Likelihood Explanation

**High.** The router is the standard, documented periphery entry point for swaps. Pool admins who want legitimate users to be able to use the router must allowlist it. The bypass requires no special privileges, no flash loans, and no complex setup — any EOA can call the router.

---

### Recommendation

The extension must check the actual end user, not the immediate pool caller. Two viable approaches:

1. **Pass the original caller in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted router convention.
2. **Check `sender` only for direct pool calls; require router to forward user identity**: Redesign the hook signature or add a separate "original sender" field that the pool populates from a trusted periphery context (e.g., transient storage set by the router before calling the pool).

The `DepositAllowlistExtension` does not share this bug because it checks `owner` (the position beneficiary explicitly supplied by the caller), not `sender` (the immediate pool caller).

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Admin calls: setAllowedToSwap(pool, alice, true)
   — alice is the only intended swapper.
3. Admin calls: setAllowedToSwap(pool, router, true)
   — required so alice can use the router.
4. Attacker (charlie, not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: charlie, ...})
5. Router calls pool.swap(charlie, ...) with msg.sender = router.
6. Pool calls extension.beforeSwap(router, ...).
7. Extension checks: allowedSwapper[pool][router] → true → passes.
8. Charlie's swap executes on the restricted pool.
   allowedSwapper[pool][charlie] was never checked.
```

**Root cause:** `SwapAllowlistExtension.beforeSwap` at line 37 checks `allowedSwapper[msg.sender][sender]` where `sender` is the pool's `msg.sender` (the router), not the actual end user. [1](#0-0) 

**Pool passes its own `msg.sender` as `sender` to the extension:** [2](#0-1) 

**Router calls `pool.swap` with `msg.sender = router`, not the end user:** [3](#0-2) 

**`DepositAllowlistExtension` correctly checks `owner` (position beneficiary), not `sender`:** [4](#0-3)

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
