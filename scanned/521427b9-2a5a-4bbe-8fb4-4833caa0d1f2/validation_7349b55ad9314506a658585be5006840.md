### Title
SwapAllowlistExtension Gates the Router Address Instead of the End-User, Allowing Any User to Bypass the Swap Allowlist on Curated Pools via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which is `msg.sender` of the pool's `swap()` call. When users swap through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the end user. If the router is allowlisted for a pool, every user—including non-allowlisted ones—can bypass the swap allowlist by routing through the router. This is the direct analog to the AMM `funding()` bug: a guard that is present in the code but is misbound to the wrong actor, silently failing open for the entire router-mediated swap path.

---

### Finding Description

**Hook plumbing — wrong-actor binding in the allowlist guard.**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `msg.sender` as the `sender` argument forwarded to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) calls `pool.swap()`, the pool's `msg.sender` is the **router**, not the end user: [4](#0-3) 

Therefore the allowlist check becomes `allowedSwapper[pool][router]`. A pool admin who wants allowlisted users to be able to use the standard router interface **must** add the router to the allowlist. Once the router is allowlisted, the check passes for **every** user who routes through it, regardless of whether that user is individually allowlisted.

There is no mechanism in the extension or the router to thread the original end-user address through to the hook. The `extensionData` bytes are user-controlled and the extension does not read them. The `BaseMetricExtension` base class declares `beforeSwap` with `onlyPool`, but `SwapAllowlistExtension` overrides it without that modifier and without any substitute identity check on the true originator: [5](#0-4) 

The deposit-side extension avoids this problem because it checks `owner` (the LP position owner passed explicitly by the caller), not `sender`. The swap-side extension has no equivalent field that carries the true economic actor through the router hop.

---

### Impact Explanation

Any non-allowlisted user can trade on a curated pool that uses `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter`. The pool's intended access control—restricting swaps to a vetted set of counterparties—is silently nullified for the entire router-mediated path. Curated pools are typically deployed to protect LP capital from adversarial or unvetted traders; bypassing the allowlist exposes LP principal to trades the pool designer explicitly prohibited. This is a direct loss-of-protection impact on LP assets and constitutes a broken core pool invariant above Sherlock thresholds.

---

### Likelihood Explanation

The bypass requires the router to be allowlisted. A pool admin who wants their vetted users to access the pool through the standard periphery interface has no alternative: they must allowlist the router, because the router is the `msg.sender` the pool sees. This is the expected operational path for any production curated pool that intends to be usable via the standard router. The bypass is therefore reachable on any curated pool that is not restricted to direct `pool.swap()` calls only, which covers the realistic production deployment surface.

---

### Recommendation

The allowlist must gate the true economic actor, not the immediate caller of `pool.swap()`. Concrete options:

1. **Standardized originator field in `extensionData`**: Define a convention where the router prepends the original `msg.sender` to `extensionData`, and have `SwapAllowlistExtension` decode and verify it (with a trust anchor on the router address).
2. **Router-level per-user check**: Add an on-chain registry that the router consults before forwarding, so only allowlisted users can initiate router swaps on allowlisted pools.
3. **Documentation and factory enforcement**: If the design intent is that allowlisted pools must not use the router, enforce this at factory creation time (e.g., reject extension configurations that pair `SwapAllowlistExtension` with a mutable router address in the allowlist).

---

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension as a configured before-swap hook.
2. Pool admin calls:
       allowedSwapper[pool][router] = true   // to let allowlisted users use the router
   Pool admin does NOT add attacker to the allowlist.
3. Attacker (non-allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
4. Router calls pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
       → pool's msg.sender = router
5. Pool calls _beforeSwap(sender=router, ...)
       → SwapAllowlistExtension.beforeSwap receives sender=router
       → checks allowedSwapper[pool][router] == true  ✓
       → hook passes; swap executes
6. Attacker receives output tokens from the curated pool despite never being allowlisted.
```

The allowlist guard ran, returned the correct selector, and the swap settled — the guard silently failed open for the wrong actor, exactly mirroring the AMM `funding()` pattern where a required state check is present in specification but misbound in implementation.

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L81-88)
```text
  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```
