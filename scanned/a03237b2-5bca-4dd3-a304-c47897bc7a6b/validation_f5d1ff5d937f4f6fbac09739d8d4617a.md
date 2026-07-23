Let me check how `_beforeSwap` is called in `ExtensionCalling` to confirm the exact `sender` argument passed to the hook.

The call chain is fully traceable. Here is the analysis:

---

## Call Chain Trace

```
User → MetricOmmSimpleRouter::exactInputSingle(params)
         └─ pool.swap(recipient, zeroForOne, amount, ..., extensionData)
              │  msg.sender in pool = router address
              └─ _beforeSwap(msg.sender=router, recipient, ...)   [MetricOmmPool.sol:230-240]
                   └─ ExtensionCalling::_beforeSwap(sender=router, ...)  [ExtensionCalling.sol:149-177]
                        └─ SwapAllowlistExtension::beforeSwap(sender=router, ...)
                             checks: allowedSwapper[msg.sender=pool][sender=router]
```

---

### Title
Router-Mediated Swaps Bypass `SwapAllowlistExtension` Per-User Allowlist Due to Sender Identity Substitution — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the first argument passed by the pool — which is always `msg.sender` of the pool's `swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router address**, not the original end user. If the router is allowlisted for a pool, any unprivileged user can bypass the per-user allowlist by routing through the router.

### Finding Description

In `MetricOmmPool::swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling::_beforeSwap` forwards this `sender` value verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension::beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` = pool, `sender` = whoever called `pool.swap()`. When the router calls `pool.swap()`, `sender` = router address.

In `MetricOmmSimpleRouter::exactInputSingle`, the router calls `pool.swap()` directly with no mechanism to forward the original caller's identity: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

### Impact Explanation

**Bypass path:** A pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a specific set of addresses. To allow those users to swap via the router (the standard UX), the admin allowlists the router address via `setAllowedToSwap(pool, router, true)`. At this point, `allowedSwapper[pool][router] = true`. Any unprivileged user can now call `router.exactInputSingle(...)` and the hook passes — because the hook sees `sender = router` (allowlisted), not the actual end user. The per-user allowlist is completely defeated.

**Breakage path (secondary):** If the admin allowlists specific user addresses but not the router, those users cannot use the router at all — the hook sees `sender = router` (not allowlisted) and reverts. This breaks the expected swap UX for allowlisted users.

Both paths constitute broken core pool functionality: the allowlist extension's sole purpose — restricting swaps to specific actors — is rendered ineffective or unusable for router-mediated flows.

### Impact Explanation

High. The allowlist is an admin-boundary access control mechanism. Its bypass by any unprivileged user via the public router is a direct admin-boundary break. Pools relying on `SwapAllowlistExtension` for permissioned swap access (e.g., KYC-gated pools, institutional pools) have no effective protection against router-mediated swaps.

### Likelihood Explanation

High. `MetricOmmSimpleRouter` is the standard public entrypoint for swaps. Any pool using `SwapAllowlistExtension` that also allowlists the router (a natural and expected configuration) is immediately vulnerable. No special timing, oracle manipulation, or multi-block sequencing is required.

### Recommendation

The `sender` passed to `beforeSwap` must represent the true originating user, not the intermediate caller. Options:

1. **Pass `tx.origin` as sender** — simple but breaks contract-to-contract composability and is generally discouraged.
2. **Router forwards original caller** — add an `extensionData`-encoded caller field that the hook reads, with the pool verifying the router's signature or identity before trusting it.
3. **Allowlist at the router level** — the hook checks `allowedSwapper[pool][msg.sender_of_router]` by having the router pass the original caller in `extensionData`, and the hook validates `msg.sender` (the pool) to ensure only trusted routers can supply this field.
4. **Separate router-allowlist from user-allowlist** — require both `allowedSwapper[pool][router]` AND a user-level check encoded in `extensionData`.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — intending to allow router-mediated swaps for allowlisted users only.
3. Unprivileged attacker (address `0xDEAD`, not in allowlist) calls `router.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` — `msg.sender` in pool = router.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. Hook checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Attacker successfully swaps on a pool they were never allowlisted for.

Assert: `allowedSwapper[pool][0xDEAD] == false` yet the swap succeeds. The invariant "only allowlisted addresses may swap" is violated.

---

**Note on the "velocity-envelope" framing in the question:** `SwapAllowlistExtension` contains no velocity envelope, per-block price-change cap, or squared-price guard. That framing does not correspond to any code in this extension. The real and only finding is the sender identity substitution described above.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
