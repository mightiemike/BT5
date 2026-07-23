### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` equals the router address, not the actual user. If the pool admin allowlists the router (which is required for any router-mediated swap to succeed), every user — including those explicitly excluded from the allowlist — can bypass the gate by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to extensions.**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with its own `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` then forwards that value verbatim as the first argument to every configured extension: [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` checks `sender`, not the real user.**

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool) as the mapping key and `sender` (the first argument) as the identity to gate: [3](#0-2) 

**Step 3 — The router is `msg.sender` of the pool call, not the user.**

`MetricOmmSimpleRouter.exactInputSingle` stores the real user in transient callback context (for payment), but calls `pool.swap(...)` directly — making the router the `msg.sender` the pool sees: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Step 4 — The allowlist therefore checks the router, not the user.**

When the router calls `pool.swap(...)`, the pool passes `msg.sender = router` as `sender` to `_beforeSwap`. The extension evaluates `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (which they must do for any allowlisted user to trade via the router), the check passes for **every caller of the router**, regardless of whether that caller is on the allowlist.

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting which addresses may swap on a pool. When the router is the intermediary, the extension's identity check is permanently misdirected to the router address. Any user who is explicitly excluded from the allowlist can bypass the gate by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). This breaks the core access-control invariant the extension is designed to enforce, allowing unauthorized swaps that drain pool liquidity at oracle-derived prices — a direct loss of LP principal.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard user-facing swap interface. A pool admin who configures a `SwapAllowlistExtension` and wants any allowlisted user to trade via the router must allowlist the router address. Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges, no malicious setup, and no non-standard tokens. The attack requires only a single public router call.

---

### Recommendation

The router must forward the real user's identity to the pool so extensions can gate on it. Two viable approaches:

1. **Transient-storage originator slot**: Before calling `pool.swap`, the router writes `msg.sender` into a well-known transient slot. The pool reads it and passes it as `sender` to extensions instead of its own `msg.sender`. The slot is cleared after the swap.

2. **Explicit originator parameter on `swap`**: Add an `originator` parameter to `IMetricOmmPoolActions.swap`. The pool passes `originator` (not `msg.sender`) to extensions. The router supplies `msg.sender`; direct callers supply `address(0)` (pool falls back to `msg.sender`).

Either approach ensures `SwapAllowlistExtension` always sees the economically relevant actor, not the router intermediary.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls: swapExtension.setAllowedToSwap(pool, router, true)
    (required so that allowlisted users can trade via the router)
  pool admin does NOT call: swapExtension.setAllowedToSwap(pool, attacker, true)

Attack:
  attacker calls:
    router.exactInputSingle({
      pool:      pool,
      recipient: attacker,
      zeroForOne: true,
      amountIn:  X,
      ...
    })

Execution trace:
  router.exactInputSingle
    → pool.swap(recipient=attacker, ...)   [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, tokens transferred to attacker

Result:
  attacker — not on the allowlist — completes a swap on a restricted pool.
  LP principal is drained at oracle price by an unauthorized counterparty.
``` [5](#0-4) [6](#0-5) [4](#0-3)

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
