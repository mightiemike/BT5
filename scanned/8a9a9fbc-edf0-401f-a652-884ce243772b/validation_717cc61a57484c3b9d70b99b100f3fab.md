### Title
SwapAllowlistExtension gates the router's address instead of the end user, making the allowlist bypassable or causing DoS for all router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is the value the pool passes as its first argument. The pool always passes `msg.sender` — the immediate caller — as that argument. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. This produces two mutually exclusive failure modes depending on how the pool admin configures the allowlist.

---

### Finding Description

**Root cause — wrong identity forwarded to the extension**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol  line 230-240
_beforeSwap(
    msg.sender,   // ← always the immediate caller of pool.swap()
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

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol  line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...)
    )
);
```

`SwapAllowlistExtension.beforeSwap` then checks that forwarded `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  line 72-80
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

Inside `pool.swap`, `msg.sender` is the **router**, so the extension receives `sender = router`. The allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**Two failure modes, both reachable by any unprivileged actor:**

| Router allowlist state | Outcome |
|---|---|
| Router **not** in allowlist | Every router-mediated swap reverts with `NotAllowedToSwap`, even for individually allowlisted users. Core swap flow is broken for all router users. |
| Router **is** in allowlist | Every user — including those the pool admin explicitly excluded — can bypass the guard by routing through `MetricOmmSimpleRouter`. |

---

### Impact Explanation

**Bypass path (router allowlisted):** Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` on a pool whose admin intended to restrict swaps to a curated set of addresses. The extension sees `sender = router` (allowlisted) and passes the check unconditionally. The pool admin's access control is completely defeated without any privileged action.

**DoS path (router not allowlisted):** Allowlisted users who interact through the router — the primary production entry point — receive `NotAllowedToSwap` on every attempt. The swap flow is unusable for them despite being individually permitted. This matches the NFTX analog: a peripheral contract's check fails for all users because the wrong identity is evaluated.

Both outcomes satisfy the allowed impact gate: broken core pool functionality causing unusable swap flows, and admin-boundary break where an unprivileged path bypasses a configured guard.

---

### Likelihood Explanation

The router is the standard user-facing entry point for swaps. Any user who calls `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` triggers this path. No special permissions, tokens, or timing are required. The trigger is a normal, everyday swap through the router on any pool that has `SwapAllowlistExtension` configured.

---

### Recommendation

The extension must check the **end user's** identity, not the immediate caller of `pool.swap`. Two complementary fixes:

1. **In `MetricOmmPool.swap`**: pass the original initiator rather than `msg.sender` as the `sender` argument to `_beforeSwap`. This requires the router to forward the user's address, e.g., via `callbackData` or a dedicated parameter — similar to how Uniswap v4 passes `msgSender` through the unlock/callback chain.

2. **Alternatively, in `SwapAllowlistExtension.beforeSwap`**: if the `sender` is a known trusted router, read the actual payer from the router's transient storage (the `_getPayer()` slot already stored there) and check that address instead. This is a lighter change but couples the extension to the router's internal layout.

The cleanest fix is option 1: the pool should propagate the original `tx.origin`-equivalent initiator through the hook arguments so extensions can gate on the economically relevant actor.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` (to allow router-mediated swaps) and does **not** allowlist `attacker`.
3. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Inside `pool.swap`, `msg.sender = router`, so `_beforeSwap(router, ...)` is called.
5. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. `attacker` successfully swaps on a pool they were never individually permitted to access.

Conversely, if the admin allowlists `alice` but not the router:
- `alice` calls `router.exactInputSingle(...)` → pool passes `sender = router` → `allowedSwapper[pool][router]` is `false` → `NotAllowedToSwap` reverts, even though Alice is individually permitted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
