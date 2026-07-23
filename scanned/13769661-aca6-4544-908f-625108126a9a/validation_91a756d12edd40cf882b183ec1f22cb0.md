The call chain is fully traceable. Here is the definitive assessment.

---

## Full Call-Chain Trace

**Direct pool call:**
```
User → MetricOmmPool.swap()
  msg.sender = user
  _beforeSwap(sender=user, ...)          [ExtensionCalling.sol:160-176]
  → extension.beforeSwap(sender=user)    [SwapAllowlistExtension.sol:31-41]
  → checks allowedSwapper[pool][user]    ✓ correct identity
```

**Router call (`exactInputSingle` / `exactInput`):**
```
User → MetricOmmSimpleRouter.exactInputSingle()
  → IMetricOmmPoolActions(pool).swap(...)   [MetricOmmSimpleRouter.sol:72-80]
  msg.sender in pool = ROUTER address
  _beforeSwap(sender=ROUTER, ...)           [ExtensionCalling.sol:160-176]
  → extension.beforeSwap(sender=ROUTER)     [SwapAllowlistExtension.sol:31-41]
  → checks allowedSwapper[pool][ROUTER]     ✗ wrong identity
```

---

## Key Code Points

`MetricOmmPool.swap()` passes `msg.sender` as `sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool and `sender` = whoever called `pool.swap()`: [3](#0-2) 

When the router calls `pool.swap()`, `msg.sender` inside the pool is the router, so `sender` forwarded to the extension is the router address — not the end user: [4](#0-3) 

---

## Assessment

### Title
Router-Mediated Swaps Replace User Identity with Router Address in `SwapAllowlistExtension.beforeSwap` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool sees the router as `msg.sender`, so the allowlist checks the router's address rather than the actual user's address. This collapses per-user granularity to a binary router-level gate.

### Finding Description
The `SwapAllowlistExtension` is designed to restrict swaps to a curated set of addresses per pool. The `sender` argument it receives is the direct caller of `MetricOmmPool.swap()`. When `MetricOmmSimpleRouter` is used, that caller is the router contract, not the end user.

This creates an irreconcilable conflict for any pool admin who wants to use the allowlist with the router:

- **If the router is NOT allowlisted**: allowlisted users cannot use the router at all — the hook reverts with `NotAllowedToSwap()` for every router-mediated swap.
- **If the router IS allowlisted**: the per-user allowlist is completely bypassed — any user can swap through the router regardless of whether their own address is in `allowedSwapper`.

The second scenario is the exploit path. A pool admin who allowlists the router (the natural step to let their approved users access the periphery) inadvertently opens the pool to all users. An unprivileged attacker calls `exactInputSingle` or `exactInput` on the router pointing at the curated pool; the hook sees `sender = router`, finds `allowedSwapper[pool][router] = true`, and permits the swap.

### Impact Explanation
Disallowed users can trade on pools that are supposed to be restricted to a curated set of swappers. This breaks the core curation guarantee of the `SwapAllowlistExtension` and constitutes broken core pool functionality — the access control the pool admin configured does not apply to the supported periphery path.

### Likelihood Explanation
Any pool that (a) uses `SwapAllowlistExtension` and (b) allowlists the router to let approved users access the periphery is vulnerable. This is the expected operational pattern for a curated pool that also wants to support the standard router UX. The attacker needs no special privileges — just calling the public router is sufficient.

### Recommendation
The extension must verify the actual end-user identity, not the immediate caller. Two options:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the router to be trusted, which it is (it is a factory-deployed periphery contract).
2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is the user, but this breaks for multi-hop paths where intermediate recipients are the router itself.
3. **Require direct pool calls for allowlisted pools**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory level.

### Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, router, true)   // to let approved users use the router
3. Attacker (not in allowedSwapper) calls:
     MetricOmmSimpleRouter.exactInputSingle({pool: curated_pool, ...})
4. Pool.swap() fires with msg.sender = router.
5. _beforeSwap(sender=router, ...) → extension checks allowedSwapper[pool][router] = true → passes.
6. Attacker's swap executes on the curated pool despite not being allowlisted.
```

---

**Verdict: Valid vulnerability.** The `sender` identity passed to `SwapAllowlistExtension.beforeSwap` is the router address when using the supported periphery path, not the actual user. This breaks the invariant that a curated pool enforces the same allowlist policy regardless of which supported public entrypoint reaches it.

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
