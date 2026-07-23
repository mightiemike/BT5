### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Allowlist Bypass — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` receives `sender` as the first argument, which the pool sets to `msg.sender` of its own `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. The guard is therefore bound to the wrong actor on every router-mediated swap.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
     → MetricOmmPool._beforeSwap(msg.sender=router, ...)
     → ExtensionCalling._beforeSwap(sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → allowedSwapper[pool][router]  ← wrong identity checked
```

**Pool passes `msg.sender` as `sender` to the extension dispatcher:** [1](#0-0) 

The dispatcher encodes `sender` (the router address) as the first argument of `IMetricOmmExtensions.beforeSwap`.

**The extension checks that encoded `sender` against the allowlist:** [2](#0-1) 

`allowedSwapper[msg.sender][sender]` evaluates to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**The router calls `pool.swap()` directly with no mechanism to forward the real caller:** [3](#0-2) 

There is no field in the call that carries `msg.sender` of the router into the pool's `sender` slot.

---

### Impact Explanation

Two fund-impacting outcomes arise from the same root cause:

**Scenario A — Allowlist bypass (security failure):** If the pool admin allowlists the router (a natural action so that allowlisted users can use the standard periphery), every non-allowlisted address can bypass the curated-pool restriction by calling `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router. The extension sees the allowlisted router and passes the check, letting unauthorized users trade in a pool that was explicitly restricted.

**Scenario B — Broken periphery for legitimate users (functionality failure):** If the admin does not allowlist the router, every allowlisted user who tries to swap through the router is rejected because the extension sees the non-allowlisted router address. The standard periphery path is unusable for any pool with `SwapAllowlistExtension` unless the admin separately allowlists the router contract, which then opens Scenario A.

Both outcomes directly affect user funds: Scenario A allows unauthorized value extraction from a curated pool; Scenario B prevents legitimate LPs and traders from using the supported swap path.

---

### Likelihood Explanation

- `SwapAllowlistExtension` is a first-party, deployed extension explicitly documented as a swap gate.
- `MetricOmmSimpleRouter` is the primary user-facing swap entry point; most users interact through it rather than calling the pool directly.
- A pool admin who wants allowlisted users to be able to use the router will naturally add the router to the allowlist, directly enabling Scenario A.
- No privileged or malicious setup is required: the attacker only needs to call the public router with a standard `exactInputSingle` call.

---

### Recommendation

The extension must gate the **economically relevant actor** — the address that initiated the trade and will receive or pay tokens — not the intermediate contract. Two approaches:

1. **Pass the real initiator through the pool:** Add an optional `initiator` field to the swap parameters or extension data that the router populates with `msg.sender` before calling the pool. The extension reads this field instead of `sender`.

2. **Check `sender` in the router, not the pool:** Have the router verify allowlist membership before calling the pool, and remove the extension from the router path. This is weaker because it is not enforced at the pool level.

The cleanest fix is approach 1: the pool's `swap()` accepts an explicit `initiator` address (defaulting to `msg.sender` for direct calls), passes it to `_beforeSwap`, and the extension checks `allowedSwapper[pool][initiator]`.

---

### Proof of Concept

```solidity
// Pool is deployed with SwapAllowlistExtension.
// Admin allowlists Alice (0xAlice) and the router (0xRouter) so Alice can use the router.
// Bob (0xBob) is NOT allowlisted.

// Direct call — correctly blocked:
pool.swap(bob, true, 1000, 0, "", "");
// → beforeSwap(sender=bob) → allowedSwapper[pool][bob] = false → revert NotAllowedToSwap ✓

// Router call — bypass:
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000,
    ...
}));
// → pool.swap(msg.sender=router, ...) 
// → beforeSwap(sender=router) → allowedSwapper[pool][router] = true → passes ✗
// Bob receives token output despite not being allowlisted.
```

The root cause is identical in structure to the referenced ConvexV1 bug: the guard function receives a reference (`sender` = router) that does not correspond to the balance/actor it is supposed to protect against (`actual_user`), so the check passes on the wrong identity.

### Citations

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
