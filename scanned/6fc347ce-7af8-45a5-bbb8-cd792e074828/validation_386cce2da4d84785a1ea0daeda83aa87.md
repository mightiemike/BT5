Looking at the codebase, I need to trace the swap allowlist guard through the router path to find the analog.

**Key code path:**

`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that `sender` to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly — making the router the `msg.sender` of that call, not the end user: [4](#0-3) 

---

### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` equals the router address, not the actual user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently grants swap access to every user, completely bypassing the allowlist guard.

### Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`, which forwards it verbatim to `SwapAllowlistExtension.beforeSwap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` with `msg.sender = router`. The extension then evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

This creates an inescapable dilemma for the pool admin:

- **If the router is NOT allowlisted**: allowlisted users cannot use the router at all (broken core swap flow).
- **If the router IS allowlisted** (the natural choice to enable router-mediated swaps): any user — regardless of allowlist status — can call the router and have their swap pass the guard, because the extension sees the allowlisted router address as `sender`.

The same issue applies to multi-hop `exactInput` and `exactOutput` paths, where intermediate hops also use the router as `msg.sender` to the pool.

### Impact Explanation
The swap allowlist guard is completely bypassed for any user who routes through `MetricOmmSimpleRouter`. A pool configured as a private or permissioned pool (e.g., only KYC'd counterparties, only whitelisted market makers) becomes effectively open to all users. This breaks the admin-boundary invariant: the pool admin's intended access control is bypassed by an unprivileged path — the public router — without any special privileges or setup required by the attacker.

### Likelihood Explanation
High. `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps in the periphery layer. Any user who wants to bypass the allowlist simply calls the router instead of the pool directly. The router must be allowlisted for any allowlisted user to use it, so the bypass condition is a natural consequence of normal pool operation.

### Recommendation
The extension must check the actual end-user identity, not the immediate caller of the pool. Options:

1. **Pass originator through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the extension to trust the router's encoding, which itself needs a factory-verified router registry.
2. **Separate originator field in hook arguments**: Add an `originator` parameter to `IMetricOmmExtensions.beforeSwap` that the pool populates from a router-supplied transient slot, similar to how the router already stores payer context in transient storage.
3. **Document the limitation explicitly**: If the design intent is that allowlisted pools must be accessed directly (no router), document this and add a revert in the extension when `sender` is a registered router address.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Admin allowlists only Alice:
       setAllowedToSwap(pool, alice, true)
3. Admin allowlists the router (required for any router-mediated swap to work):
       setAllowedToSwap(pool, router, true)
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
5. Router calls pool.swap(...) — msg.sender to pool = router.
6. Pool calls extension.beforeSwap(router, bob, ...).
7. Extension evaluates: allowedSwapper[pool][router] == true → passes.
8. Bob's swap executes successfully despite not being on the allowlist.
```

The root cause is at: [5](#0-4) 

where `sender` is the router address for all router-mediated swaps, and at: [6](#0-5) 

where `msg.sender` (the router) is unconditionally forwarded as the `sender` identity to all extension hooks.

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
