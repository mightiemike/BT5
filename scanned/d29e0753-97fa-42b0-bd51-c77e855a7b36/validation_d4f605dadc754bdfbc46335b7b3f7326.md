### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end-user, allowing any unprivileged caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is intended to gate which addresses may swap on a pool. Its `beforeSwap` hook checks the `sender` argument, which the pool sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **actual swapper** is allowlisted. This creates a binary failure: either the router is not allowlisted (breaking all router-mediated swaps for every user, including legitimately allowlisted ones), or the router is allowlisted (granting every user on the internet a universal bypass of the allowlist).

---

### Finding Description

**Hook argument binding — `sender` is the pool's immediate caller, not the economic actor**

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
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

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol L88-98
abi.encodeCall(IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, amountSpecified,
     priceLimitX64, packedSlot0Initial, bidPriceX64, askPriceX64, extensionData))
```

`SwapAllowlistExtension.beforeSwap` then uses that `sender` as the identity to check:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (correct for pool-keyed storage). `sender` is whoever called `pool.swap()`.

**The router path**

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly:

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

The router does not forward the original `msg.sender` (the end user) anywhere in the call. The pool therefore receives `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]`.

**The two failure modes**

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Every router-mediated swap reverts with `NotAllowedToSwap`, even for individually allowlisted users. Legitimate users must implement `IMetricOmmSwapCallback` themselves to call the pool directly. |
| Router **allowlisted** | `allowedSwapper[pool][router] = true` passes for every caller of the router, regardless of whether the end user is on the allowlist. The allowlist is a no-op for all router-mediated swaps. |

There is no middle ground: the router is a single address, so allowlisting it is an all-or-nothing decision.

---

### Impact Explanation

A pool admin who deploys a pool with `SwapAllowlistExtension` intends to restrict swapping to a specific set of addresses (e.g., KYC-verified counterparties, institutional LPs, or a private trading desk). Once the router is allowlisted (which is required for any user to use the standard periphery), every address on the internet can bypass the restriction by calling `MetricOmmSimpleRouter`. The allowlist guard is completely ineffective for the router path, which is the primary user-facing entry point. Non-allowlisted users can drain the pool's liquidity through unrestricted swaps, directly impacting LP principal.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the standard, documented swap entry point for end users.
- Pool admins must allowlist the router to make the pool usable via the periphery; the alternative (requiring every user to write their own callback) is not a realistic operational posture.
- Any unprivileged user can call the router with no special setup.
- The bypass requires zero privileged access and zero special tokens.

---

### Recommendation

The extension must check the **economic actor** (the end user), not the **intermediate caller** (the router). Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Add a dedicated `swapper` field to the hook signature**: Extend `IMetricOmmExtensions.beforeSwap` with an explicit `swapper` parameter that the pool populates from a separate, user-supplied argument (distinct from `sender`). The router passes the original `msg.sender` as `swapper`.

3. **Restrict the allowlist to direct pool callers only**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and revert if `sender` is a known router. This is the least invasive fix but requires maintaining a router registry.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, user1, true)   // only user1 is allowed
  admin calls setAllowedToSwap(pool, router, true)  // required for router to work

Attack:
  user2 (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        recipient: user2,
        zeroForOne: true,
        amountIn: X,
        ...
    })

  Router calls pool.swap() with msg.sender = router
  Pool calls _beforeSwap(sender=router, ...)
  Extension checks allowedSwapper[pool][router] → true
  Swap executes successfully for user2

Result:
  user2 bypasses the allowlist and swaps on a restricted pool.
  The allowlist provides zero protection for any router-mediated swap.
```