### Title
SwapAllowlistExtension Gates the Router Address Instead of the Real Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the actual user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every user on the internet can bypass the allowlist by routing through the router.

---

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(recipient, zeroForOne, amount, limit, "", extensionData)` — `msg.sender` inside the pool is the **router address**. [1](#0-0) 

3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` — passing the **router address** as `sender`. [2](#0-1) 

4. `ExtensionCalling._beforeSwap` encodes `sender` (= router) and dispatches to `SwapAllowlistExtension.beforeSwap`. [3](#0-2) 

5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][router]`. [4](#0-3) 

The extension is documented as "Gates `swap` by swapper address, per pool." The intended gated identity is the **real user**. The actual gated identity when the router is used is the **router contract address**.

**The structural mismatch:** The pool admin who wants to restrict swaps to a curated set of users must also decide whether to allowlist the router. If they do allowlist the router (the only way to let allowlisted users use the standard periphery), the allowlist is completely open to every user who calls the router. If they do not allowlist the router, allowlisted users cannot use the router at all — they must call the pool directly, which requires implementing the `IMetricOmmSwapCallback` interface themselves.

This is the direct analog to the external report's bug: the wrong entity is being checked (router instead of actual user), just as the wrong algorithm was applied (simple round-robin instead of weighted round-robin). In both cases, the configured guard operates on a proxy identity rather than the economically relevant one.

---

### Impact Explanation

A pool deployed with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, whitelisted market makers, or protocol-controlled addresses) is fully bypassed by any unprivileged user who calls `MetricOmmSimpleRouter.exactInputSingle` / `exactOutputSingle` / `exactInput` / `exactOutput`. The attacker receives the pool's output tokens and pays the pool's input tokens — a complete swap — without being on the allowlist. The pool's LP funds are exposed to unrestricted trading, defeating the entire purpose of the curation guard.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps.
- Any user who reads the contract or the README will discover the router path.
- No special privilege, flash loan, or multi-block setup is required — a single `exactInputSingle` call suffices.
- The pool admin has no on-chain mechanism to detect or prevent this; the allowlist storage is keyed correctly but the wrong address is looked up at runtime.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **original user**, not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the router.** The router already stores the original `msg.sender` in transient storage as the payer (`_setNextCallbackContext(..., msg.sender, ...)`). The pool's `swap` interface could accept an explicit `originator` parameter, or the extension could read it from a trusted transient slot. The simpler approach is for the router to pass the real user address inside `extensionData`, and for the extension to decode and check that address — but this requires the extension to trust the router, which reintroduces the same problem unless the router is verified.

2. **Preferred fix:** Change the allowlist key from `sender` (the immediate pool caller) to a value that cannot be spoofed by an intermediary. The cleanest solution matching the design intent is to check `tx.origin` as a fallback when `sender` is a known contract, or — better — require that the pool's `swap` function accept and forward an explicit `swapper` address that the router populates with `msg.sender` before calling the pool.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to allow router-mediated swaps
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not on allowlist) calls:
      router.exactInputSingle(ExactInputSingleParams({
          pool: pool,
          recipient: attacker,
          tokenIn: token0,
          zeroForOne: true,
          amountIn: X,
          amountOutMinimum: 0,
          priceLimitX64: 0,
          deadline: block.timestamp,
          extensionData: ""
      }))

Execution trace:
  router.exactInputSingle
    → pool.swap(recipient=attacker, ..., sender=router)   // msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (passes)
      → swap executes, attacker receives tokens

Result:
  attacker completes a swap on a pool that was supposed to restrict trading to allowlisted addresses only.
  The allowlist guard is silently bypassed.
``` [5](#0-4) [6](#0-5) [1](#0-0)

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
