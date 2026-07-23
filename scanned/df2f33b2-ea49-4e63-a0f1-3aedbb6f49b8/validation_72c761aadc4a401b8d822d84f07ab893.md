### Title
SwapAllowlistExtension gates on the router's address instead of the actual end-user, allowing any user to bypass the per-pool swap allowlist through MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool. The pool always passes its own `msg.sender` as `sender`. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. The allowlist therefore checks whether the router is permitted, not whether the actual swapper is permitted. If the router is allowlisted (the natural configuration for a pool that supports periphery routing), every user — including explicitly blocked ones — can bypass the allowlist by routing through the router.

---

### Finding Description

**Call chain:**

```
Alice → MetricOmmSimpleRouter.exactInputSingle()
          → pool.swap(recipient, zeroForOne, amount, ..., extensionData)
               msg.sender = router
               → _beforeSwap(msg.sender=router, recipient, ...)
                    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                         checks allowedSwapper[pool][router]  ← router, not Alice
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct key for the mapping) and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][alice]`.

A pool admin who wants to support periphery routing will naturally call `setAllowedToSwap(pool, router, true)`. From that moment, the allowlist is completely inoperative: every user who routes through the router is implicitly allowlisted regardless of their individual entry.

---

### Impact Explanation

**Direct loss of curation policy and potential fund impact on restricted pools.** A pool deployer may use the swap allowlist to restrict trading to KYC'd counterparties, whitelisted market makers, or protocol-controlled addresses. Once the router is allowlisted (required for normal UX), any address — including sanctioned or explicitly blocked ones — can execute swaps against the pool's liquidity. This constitutes a broken core access-control invariant: the allowlist fails to gate the economically relevant actor (the end user) and instead gates the intermediary (the router). Unauthorized swaps drain LP assets at oracle-quoted prices, constituting direct loss of LP principal if the pool was intended to be restricted.

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the standard periphery swap entry point. Any pool admin who wants users to be able to swap through the router must allowlist it. The documentation for `SwapAllowlistExtension` does not warn that allowlisting the router opens the gate to all users. The misconfiguration is therefore the expected, natural configuration, not an edge case.

---

### Recommendation

The extension must resolve the actual end-user identity, not the intermediary. Two complementary fixes:

1. **Short term:** In `SwapAllowlistExtension.beforeSwap`, do not rely solely on the `sender` argument. Require that the pool passes the original caller through a trusted mechanism, or document explicitly that the router must never be allowlisted and that users must call the pool directly.

2. **Long term:** Redesign the `sender` binding so that when the router calls the pool, it forwards the original `msg.sender` in a verifiable way (e.g., via a trusted-forwarder pattern or by having the router pass the user address in `extensionData` with a pool-level signature check). Alternatively, the allowlist extension should check `recipient` or a user-supplied identity field that the router explicitly populates with the original caller, rather than the pool's `msg.sender`.

---

### Proof of Concept

```
Setup:
  pool deployed with SwapAllowlistExtension
  admin: setAllowedToSwap(pool, router, true)   // router allowlisted for UX
  admin: setAllowedToSwap(pool, alice, false)    // alice explicitly blocked

Attack:
  alice calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)
         pool._beforeSwap(sender=router, ...)
           extension.beforeSwap(sender=router, ...)
             allowedSwapper[pool][router] == true  → no revert
    → swap executes; alice receives output tokens

Result:
  alice bypassed the allowlist; LP assets transferred at oracle price to alice
  allowedSwapper[pool][alice] == false was never consulted
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at [1](#0-0)  where `sender` is the pool's `msg.sender` (the router), not the originating user. The pool sets this value at [2](#0-1)  by passing `msg.sender` directly. The router, which becomes that `msg.sender`, calls the pool at [3](#0-2)  without forwarding the original caller's identity. The `onlyPool` guard in `BaseMetricExtension` [4](#0-3)  correctly restricts who can call the extension but does nothing to verify that the `sender` argument reflects the true end user.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
