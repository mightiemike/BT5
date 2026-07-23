### Title
SwapAllowlistExtension Checks Router Address Instead of Real Swapper, Allowing Any User to Bypass Curated-Pool Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` is the router address — not the actual user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for curated pools), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension`**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

**How `sender` is populated**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`. `msg.sender` is whoever called `swap()` on the pool:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` encodes that value as the `sender` argument forwarded to every extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)
    )
);
``` [3](#0-2) 

**Router path substitutes itself as `sender`**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly — the pool's `msg.sender` is the router, not the user:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. The router never forwards the original `msg.sender` to the pool; the pool's `swap()` signature has no `sender` parameter — it always uses `msg.sender`.

**The bypass**

The pool admin of a curated pool must choose one of two broken states:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all — broken UX |
| **Allowlist the router** | Every unprivileged user bypasses the allowlist by routing through the router |

In the second (natural) case, `allowedSwapper[pool][router] == true`, so the check `!allowedSwapper[msg.sender][sender]` passes for every caller who routes through the router, regardless of whether that caller is on the allowlist.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, institutional LPs, or protocol-controlled addresses) is fully bypassed. Any unprivileged user can trade against the pool's liquidity by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). The pool's LP principal is exposed to unrestricted counterparties, and any price advantage or risk management the allowlist was meant to enforce is nullified. This is a direct loss-of-curation-policy impact with potential LP fund loss if the pool was priced for a restricted audience.

---

### Likelihood Explanation

The router is the standard, documented periphery entry point for swaps. Any pool admin who wants allowlisted users to be able to use the router (the expected UX) will allowlist the router address. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call `exactInputSingle` on the router pointing at the curated pool.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the **economic actor** (the end user), not the intermediary. Two complementary fixes:

1. **Router-level**: `MetricOmmSimpleRouter` should accept and forward the original `msg.sender` as an explicit `swapper` field in `extensionData`, and `SwapAllowlistExtension` should decode and check that field when present.

2. **Extension-level (simpler)**: `SwapAllowlistExtension.beforeSwap` should check `tx.origin` as a fallback when `sender` is a known router, or the pool admin documentation must explicitly warn that allowlisting the router opens the pool to all users. A cleaner design is to have the router pass `msg.sender` through `extensionData` and have the extension decode it.

The cleanest fix is to have the router encode the real user address into `extensionData` and have `SwapAllowlistExtension` decode and gate on that value instead of (or in addition to) `sender`.

---

### Proof of Concept

```
1. Pool admin deploys a curated pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)  — only alice is allowed.
3. Pool admin calls setAllowedToSwap(pool, router, true) — to let alice use the router.
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: curatedPool, ...}).
5. The router calls pool.swap(...) — pool's msg.sender = router.
6. _beforeSwap(router, ...) is dispatched to SwapAllowlistExtension.
7. Extension checks allowedSwapper[pool][router] == true → passes.
8. Bob's swap executes against the curated pool's liquidity.
9. The allowlist invariant is violated: Bob, who is not allowlisted, traded on the curated pool.
```

Call path:
```
Bob → MetricOmmSimpleRouter.exactInputSingle()
        → MetricOmmPool.swap()          [msg.sender = router]
            → _beforeSwap(sender=router, ...)
                → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                    allowedSwapper[pool][router] == true → PASS
            → swap executes for Bob
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
