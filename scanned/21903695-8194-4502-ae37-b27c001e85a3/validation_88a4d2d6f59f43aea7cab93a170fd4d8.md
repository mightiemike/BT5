### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Complete Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the allowlist checks the **router's address** rather than the **actual end user**. If the router is allowlisted (the natural production setup for a pool that supports router-based swaps), any unprivileged user can bypass the curated allowlist entirely.

---

### Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` then ABI-encodes that same `sender` value and forwards it to every configured extension: [2](#0-1) 

**Step 2 — The router is `msg.sender` to the pool.**

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly. The actual end user (`msg.sender` to the router) is stored only in transient storage as the payer; it is never forwarded to the pool or to any extension: [3](#0-2) 

**Step 3 — The allowlist checks the router, not the user.**

`SwapAllowlistExtension.beforeSwap()` evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the pool's caller — the router: [4](#0-3) 

The effective check becomes `allowedSwapper[pool][router]`. The individual user's address is never consulted.

---

### Impact Explanation

**Allowlist bypass (Critical/High):** A pool admin who wants to support router-based swaps on a curated pool must allowlist the `MetricOmmSimpleRouter` address. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call that arrives through the router, regardless of who the actual end user is. Any unprivileged, non-allowlisted address can then bypass the curated gate by calling `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router. The allowlist provides zero protection for router-mediated swaps.

**Allowlist over-restriction (High):** If the admin does not allowlist the router, individually allowlisted users cannot use the router at all, breaking the primary user-facing swap interface for the pool.

Both outcomes break the core invariant that a curated pool enforces the same allowlist policy regardless of which supported public entrypoint reaches it.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing interface. Any pool admin who deploys a curated pool and wants users to be able to trade through the router will naturally allowlist the router address, unknowingly opening the bypass to all users. The trigger requires no special privilege — any EOA can call the router.

---

### Recommendation

The `SwapAllowlistExtension` must check the actual end user, not the intermediate router. Two viable approaches:

1. **Pass the originating user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the router to be trusted to supply the correct address.

2. **Check `recipient` instead of `sender`:** For single-hop swaps the recipient is often the actual user, though this breaks for multi-hop paths where intermediate recipients are the router itself.

3. **Redesign the hook signature** to include a separate `originator` field that the pool populates from a trusted periphery registry, analogous to how `addLiquidity` separately exposes `sender` and `owner` so the deposit allowlist can check the economically relevant actor (`owner`) independently of who called the pool.

The deposit allowlist avoids this problem because `addLiquidity` exposes both `sender` (the adder contract) and `owner` (the position owner), and the extension checks `owner`: [5](#0-4) 

The swap path has no equivalent `owner` field, leaving the extension with no way to recover the true user identity.

---

### Proof of Concept

```
1. Deploy a MetricOmmPool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to enable router-based swaps for allowlisted users.
3. Alice (not individually allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
   The router calls pool.swap(...) with msg.sender = router.
   The pool calls extension.beforeSwap(router, ...).
   The extension evaluates allowedSwapper[pool][router] == true → passes.
4. Alice's swap executes on the curated pool despite never being allowlisted.
5. Repeat for any arbitrary address — the allowlist is fully bypassed for
   all router-mediated swaps.
```

The root cause is at: [6](#0-5) 

combined with the pool's unconditional use of `msg.sender` as `sender`: [7](#0-6) 

and the router's omission of the actual user from the pool call: [8](#0-7)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
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
