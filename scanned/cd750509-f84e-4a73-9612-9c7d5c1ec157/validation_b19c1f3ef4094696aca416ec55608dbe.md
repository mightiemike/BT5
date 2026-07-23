### Title
`SwapAllowlistExtension` checks the router's address instead of the end-user's address, allowing any user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of the pool is the router contract, not the end user. If the pool admin allowlists the router (a natural step to let users use the standard periphery), every user — including those explicitly excluded from the allowlist — can swap freely through the router, making the per-user allowlist completely ineffective.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact address against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

`msg.sender` of `pool.swap()` is the router, so `sender` delivered to the extension is the router's address. The extension has no visibility into the original `msg.sender` of the router call (the actual end user). The router forwards `extensionData` verbatim but the extension ignores it entirely.

**Bypass path:**
1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists only KYC'd addresses.
2. Pool admin also allowlists the router address so that allowlisted users can use the standard periphery (a natural operational step).
3. Any non-KYC'd user calls `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool.
4. The extension sees `sender = router`, which is allowlisted → check passes → swap executes.

The allowlist is completely bypassed for every user who routes through the router.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` for access control (e.g., KYC, institutional-only, or regulatory compliance) loses all per-user gating the moment the router is allowlisted. Any unprivileged user can execute swaps against the curated pool, draining LP value or violating the pool's intended access policy. This is a direct, fund-impacting bypass of a core protection mechanism.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard user-facing entry point for swaps. Pool admins who want allowlisted users to use the router must add the router to the allowlist — there is no other mechanism. This is a predictable operational step that silently opens the pool to all users. The bypass requires no special privileges, no malicious setup, and no non-standard tokens; any user with a standard ERC-20 approval can exploit it.

---

### Recommendation

The extension must resolve the actual end-user identity rather than the immediate caller. Two approaches:

1. **Encode the real sender in `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and verifies it (requires router cooperation and cannot be spoofed if the extension trusts only the pool's forwarded data).

2. **Check `recipient` instead of `sender`**: For swap allowlists the economically relevant actor is often the recipient of output tokens; gating on `recipient` is harder to spoof through a router.

3. **Deprecate router-level allowlisting**: Document that the router cannot be allowlisted on curated pools and that allowlisted users must call the pool directly.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension (beforeSwap order set)
  allowedSwapper[pool][router] = true   // admin allowlists router for UX
  allowedSwapper[pool][alice]  = true   // alice is KYC'd
  allowedSwapper[pool][bob]    = false  // bob is NOT KYC'd

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient=bob, ...)
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true → PASSES
    → swap executes for bob despite bob not being allowlisted

Result:
  bob swaps successfully in a pool he should be barred from.
  The per-user allowlist is completely ineffective for router-mediated swaps.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
