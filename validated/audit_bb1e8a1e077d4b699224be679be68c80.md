### Title
`SwapAllowlistExtension` allowlist bypass via router-mediated swaps: `sender` is always the router address, not the actual user — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. However, `MetricOmmPool.swap` passes `msg.sender` as `sender`, which is the `MetricOmmSimpleRouter` contract address whenever a user routes through the router. Because the extension sees the router address instead of the actual user, any disallowed user can bypass the per-user allowlist by routing through the router if the router address is allowlisted — which is the only way to support router-mediated swaps for legitimately allowed users.

### Finding Description

**Invariant broken:** A curated pool's swap allowlist must enforce the same per-user policy regardless of which supported public entrypoint reaches it.

**Root cause — wrong actor binding:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // <-- always the immediate caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// ExtensionCalling.sol:163-165
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks this `sender` against the allowlist:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the **router contract**, not the end user:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

So `sender` in `beforeSwap` is always `address(router)` for every router path (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

**The forced dilemma and bypass:**

A pool admin who wants to allow only specific users to swap faces an impossible choice:

- **Option A — do not allowlist the router:** Allowed users cannot use the router at all; they must call `pool.swap` directly.
- **Option B — allowlist the router:** `allowedSwapper[pool][router] = true` is set. Now `beforeSwap` passes for any `sender = router`, meaning **every user** who routes through the router bypasses the per-user gate.

Under Option B, a disallowed user `Bob` calls `router.exactInputSingle(pool, ...)`. The pool sees `msg.sender = router`, the extension checks `allowedSwapper[pool][router] = true`, and the swap succeeds — the allowlist is fully bypassed.

### Impact Explanation

Any disallowed user can swap on a curated pool protected by `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter`. The pool admin cannot simultaneously enforce per-user swap restrictions and support the standard router entrypoint. Disallowed users gain full swap access to pools intended to be restricted, directly trading against LP assets at oracle-derived prices. This is a direct loss of curation policy and exposes LP funds to trades the pool was configured to prevent.

### Likelihood Explanation

The bypass is reachable by any unprivileged user. The only precondition is that the pool admin has allowlisted the router address — a natural and expected action for any pool that wants to support the standard periphery swap path for its allowed users. The router is a public, factory-verified contract, so allowlisting it is a routine operational step. No malicious setup is required; the attacker simply calls the public router with a valid swap.

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the **economic actor** (the end user), not the immediate `msg.sender` of `pool.swap`. Two complementary fixes:

1. **In the router:** Pass the original `msg.sender` (the end user) as an explicit `sender` field in `extensionData`, and have the extension decode it. Alternatively, use a dedicated router-aware extension interface that receives the original initiator.

2. **In `SwapAllowlistExtension`:** Gate by the `recipient` or by a user-supplied identity field in `extensionData` rather than the raw `sender` argument, since `sender` is the router when the router is used.

The cleanest fix is to have `MetricOmmSimpleRouter` forward the original `msg.sender` inside `extensionData` and have `SwapAllowlistExtension` decode and check that value instead of the `sender` parameter.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin: allowedSwapper[pool][alice] = true
  admin: allowedSwapper[pool][router] = true  ← required for alice to use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  pool.swap(msg.sender=router, ...)
    → _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  ← passes!
    → bob's swap executes against LP assets

Result:
  bob swaps on a pool he was explicitly excluded from.
  The allowlist is fully bypassed via the public router.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
