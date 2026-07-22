### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the original swapper, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the original user. The `SwapAllowlistExtension` checks `allowedSwapper[pool][sender]`, which resolves to the router's allowlist status. Any pool admin who allowlists the router to support router-mediated swaps for their approved users inadvertently grants swap access to **all** users, completely defeating the allowlist guard.

---

### Finding Description

**Root cause — identity mismatch in the hook dispatch:**

In `MetricOmmPool.swap()`, the `sender` forwarded to the extension hook is always `msg.sender` of the pool call:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
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

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol lines 72-80
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

So from the pool's perspective, `msg.sender = router address`. The `SwapAllowlistExtension.beforeSwap` hook receives `sender = router` and checks `allowedSwapper[pool][router]`.

**The predictable configuration trap:**

A pool admin who deploys a swap allowlist and also wants to support the standard periphery router for their approved users has two bad options:

1. **Do not allowlist the router** → allowlisted users cannot use the router at all (usability break).
2. **Allowlist the router** → the allowlist check passes for `sender = router` regardless of who the original caller is, so **any** user can bypass the allowlist by routing through the public `MetricOmmSimpleRouter`.

Option 2 is the natural choice for any admin who wants their allowlisted users to have a smooth UX. It is a predictable, non-malicious configuration mistake that completely voids the guard.

**Multi-hop exact-output path compounds the issue:**

In `exactOutput`, intermediate hops call `pool.swap(msg.sender, ...)` where `msg.sender` is the pool that triggered the callback — not the router and not the original user. Each hop in a multi-hop path presents a different `sender` identity to the allowlist, making consistent enforcement impossible without out-of-band identity forwarding.

```solidity
// MetricOmmSimpleRouter.sol lines 220-228
(int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
    .swap(
        msg.sender,   // ← the calling pool, not the original user
        zeroForOne,
        ...
    );
```

---

### Impact Explanation

When the router is allowlisted (the natural configuration for any pool that wants to support periphery UX), the `SwapAllowlistExtension` guard is completely bypassed for all users. Any non-allowlisted actor can:

- Swap against a pool that was intended to be restricted to specific counterparties.
- Drain LP funds through unauthorized swaps at oracle-derived prices.
- Undermine any access-control invariant the pool admin intended to enforce (e.g., KYC-gated pools, partner-only pools, or pools with restricted swap directions).

This is a direct loss of LP assets above Sherlock thresholds when the pool holds material liquidity and the allowlist was the primary access control mechanism.

---

### Likelihood Explanation

Likelihood is **medium-high**. The `MetricOmmSimpleRouter` is the canonical user-facing swap interface. Any pool admin who:

1. Deploys a `SwapAllowlistExtension` to restrict swap access, **and**
2. Wants their approved users to use the standard router (the expected UX path),

will allowlist the router address. This is not a malicious or exotic configuration — it is the obvious operational choice. The structural mismatch between "who the allowlist intends to gate" and "who the hook actually checks" is a predictable trap that requires no attacker sophistication to exploit.

---

### Recommendation

The `SwapAllowlistExtension` must check the **original user's identity**, not the intermediary router's address. Concrete options:

1. **Forward original caller via `extensionData`:** Require the router to encode `msg.sender` into `extensionData` and have the extension verify it. The pool already forwards `extensionData` unchanged to the hook.
2. **Check `recipient` instead of `sender`:** If the economically relevant actor is the recipient of the output token, gate on `recipient`. This is swap-direction-dependent and may not always be correct.
3. **Document and enforce router incompatibility:** Add a revert in `MetricOmmSimpleRouter` when the target pool has a `SwapAllowlistExtension` configured, preventing the mismatch from being silently exploitable.
4. **Introduce a canonical identity-forwarding standard:** Define an `extensionData` prefix that periphery contracts must populate with the original `msg.sender`, and have the extension validate it against a router registry.

---

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension; set allowAll = false.
  2. Allowlist only `alice` (a specific approved user) for this pool.
  3. Also allowlist the router address (to let alice use the standard UX).

Attack:
  4. `bob` (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  5. Router calls pool.swap(...); pool sees msg.sender = router.
  6. _beforeSwap passes sender = router to SwapAllowlistExtension.beforeSwap.
  7. Extension checks allowedSwapper[pool][router] → true (router was allowlisted in step 3).
  8. Hook returns success selector; swap executes.
  9. Bob successfully swaps against the restricted pool, bypassing the allowlist entirely.

Expected (correct) behavior: step 7 should check allowedSwapper[pool][bob] → false → revert.
Actual behavior: the router's allowlist status is checked, not bob's.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
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
