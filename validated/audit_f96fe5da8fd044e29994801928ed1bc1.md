### Title
SwapAllowlistExtension Gates on the Router Address Instead of the Actual End-User, Allowing Any User to Bypass the Swap Allowlist via the Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` — and therefore the `sender` forwarded to the extension — is the **router contract**, not the actual end-user. This means the allowlist either (a) blocks all router-mediated swaps even for allowlisted users, or (b) if the router is allowlisted to enable legitimate use, every unpermissioned user can bypass the curated pool's access control by routing through the router.

---

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. The router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — so `msg.sender` inside `MetricOmmPool.swap` is the **router address**.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, forwarding the router address as `sender`.
4. `ExtensionCalling._beforeSwap` encodes and dispatches `IMetricOmmExtensions.beforeSwap(sender=router, ...)` to every configured extension.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][router]`.

The actual end-user who initiated the transaction is never consulted. The allowlist check is performed on the **intermediary** (the router), not the **principal** (the user).

**Consequence:**

- If the pool admin does **not** allowlist the router: allowlisted users cannot use the router at all; they must call the pool directly. This breaks the standard UX path.
- If the pool admin **does** allowlist the router (the only way to enable router-mediated swaps for legitimate users): the allowlist is completely bypassed — any address can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router and swap against the curated pool without being on the allowlist.

This is a direct analog to the biometric bypass: the configured guard (`SwapAllowlistExtension`) trusts the intermediary identity (the router, like the device's biometric subsystem) rather than verifying the actual principal (the end-user, like the legitimate device owner). Changing the intermediary (routing through the router) is sufficient to bypass the guard entirely.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., a private OTC pool, a KYC-gated pool, or a pool with restricted LP access) is rendered fully open to any caller via the router. Any unpermissioned user can execute swaps at oracle-derived prices, draining LP value or extracting arbitrage that the pool admin intended to gate. This is a **direct loss of LP principal** and a **broken core pool functionality** (the allowlist invariant is the pool's primary safety boundary).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, documented entry point for swaps. Any user who discovers the pool and wants to trade will naturally use the router. The bypass requires no special knowledge, no privileged access, and no multi-step setup — a single `exactInputSingle` call suffices.

---

### Recommendation

The `sender` argument forwarded to extensions must represent the **economic principal**, not the intermediary. Two complementary fixes:

1. **In the router**: pass the original `msg.sender` (the end-user) as the `sender` argument to `pool.swap`, not the router itself. The pool's swap signature already accepts a `recipient` separately, so `sender` can be set to the true originator.
2. **In `SwapAllowlistExtension`**: document and enforce that the `sender` argument is the address whose allowlist membership is checked, and add an integration test that exercises the router path against an allowlisted pool.

---

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — necessary to allow any router-mediated swap for legitimate users.
3. Unpermissioned attacker (not on the allowlist) calls:
     MetricOmmSimpleRouter.exactInputSingle({
       pool: curated_pool,
       recipient: attacker,
       zeroForOne: true,
       amountIn: X,
       ...
     })
4. Pool.swap is called with msg.sender = router.
5. _beforeSwap(router, ...) is dispatched.
6. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
7. Attacker receives token output without being on the allowlist.
```

**Exact corrupted value:** `allowedSwapper[pool][router]` is `true` (set by admin to enable legitimate router use), so the guard evaluates to "allowed" for every caller regardless of their individual allowlist status. The actual end-user address is never read from storage. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
