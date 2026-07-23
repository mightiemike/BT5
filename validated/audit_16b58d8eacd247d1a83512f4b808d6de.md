The call chain is fully traceable. Here is the complete analysis:

---

### Title
Router-Mediated Swapper Identity Confusion Bypasses `SwapAllowlistExtension` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

When a swap is routed through `MetricOmmSimpleRouter`, the `beforeSwap` hook in `SwapAllowlistExtension` checks the **router's address** against the allowlist instead of the actual end-user's address. This allows any unprivileged user to bypass a pool's swap allowlist by routing through the router, completely defeating the access-control invariant the extension is designed to enforce.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the hook.**

In `MetricOmmPool::swap()`, the immediate caller (`msg.sender`) is forwarded as the `sender` argument to `_beforeSwap`: [1](#0-0) 

**Step 2 — `ExtensionCalling` encodes and dispatches that `sender` to the extension.** [2](#0-1) 

**Step 3 — `SwapAllowlistExtension::beforeSwap` checks `allowedSwapper[pool][sender]`.**

`msg.sender` inside the extension is the pool; `sender` is whoever called `pool.swap()`. [3](#0-2) 

**Step 4 — The router calls `pool.swap()` directly, making itself `msg.sender` to the pool.**

In `exactInputSingle`, `exactOutputSingle`, and every `exact*` variant, the router is the direct caller of `pool.swap()`. The actual end-user's address is stored only in transient storage for the payment callback and is never forwarded to the pool or the hook: [4](#0-3) 

**Result:** The allowlist check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Exploit path:**
- Pool admin configures `SwapAllowlistExtension` to restrict swaps to a set of known addresses (e.g., institutional traders).
- Pool admin also allowlists the router address so that router-mediated swaps work for those traders.
- Any unprivileged user calls `exactInputSingle` (or any `exact*` function) through the router.
- The hook sees `sender = router`, finds it allowlisted, and passes — the unprivileged user's swap executes.

The allowlist is completely bypassed for any pool that allowlists the router. Conversely, if the router is *not* allowlisted, legitimately allowlisted users cannot use the router at all — there is no configuration that makes the extension work correctly with the router.

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting swap access to specific addresses on a per-pool basis. The identity confusion means the invariant `"only allowlisted addresses may swap"` is broken for all router-mediated swaps. Any user can execute swaps on a restricted pool by routing through `MetricOmmSimpleRouter`, leading to unauthorized trade execution and potential fund loss for LPs in pools designed to be access-controlled.

---

### Likelihood Explanation

The router is the standard, documented entry point for end-users. Any pool that uses `SwapAllowlistExtension` and also needs to support router-mediated swaps (the common case) is vulnerable. No special privileges, flash loans, or oracle manipulation are required — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must verify the actual end-user, not the immediate caller. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it, but only when `msg.sender` (the pool's caller) is a trusted router. This requires a trusted-router registry.

2. **Check `recipient` instead of `sender`**: If the pool's design guarantees that `recipient` is always the economic beneficiary, the allowlist can gate on `recipient`. However, this must be validated against the full call graph.

3. **Require direct pool interaction**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory level (e.g., reject pool configurations that combine this extension with a router allowlist entry).

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, router, true)  // allowlist the router
3. Pool admin does NOT allowlist attacker EOA.
4. Attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(...) — msg.sender to pool = router
6. beforeSwap receives sender = router
7. allowedSwapper[pool][router] == true → hook passes
8. Attacker's swap executes on the restricted pool.
```

Direct assertion: `allowedSwapper[pool][attacker] == false` yet the swap succeeds, violating the allowlist invariant.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
