### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of `pool.swap()`, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. If the pool admin allowlists the router address to support router-mediated swaps for curated users, every unprivileged user can bypass the allowlist entirely by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` entry point) calls `pool.swap()`, the router is `msg.sender` of that call: [4](#0-3) 

So the allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The actual user's address is never consulted.

This creates two mutually exclusive failure modes:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Every allowlisted user is silently blocked when they use the router — broken core swap flow |
| Router **allowlisted** (to support router-mediated swaps) | Every non-allowlisted user can bypass the guard by routing through the router |

---

### Impact Explanation

A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for all pools that share this extension instance. Any unprivileged user can then call `router.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting the curated pool and the `beforeSwap` hook passes unconditionally. The curated pool's swap restriction is completely defeated, allowing unauthorized traders to execute swaps against LP positions that were never intended to be exposed to the open market. This is a direct loss-of-principal risk for LPs on restricted pools.

---

### Likelihood Explanation

The `SwapAllowlistExtension` is the production reference implementation for curated pools. Any operator who deploys a curated pool and also wants to support the standard router UX will encounter the dilemma: either block the router entirely (breaking the standard user flow) or allowlist the router (opening the bypass). The bypass requires only a single public `router.exactInput*` call with no special privileges, tokens, or timing. Likelihood is medium-high once any curated pool allowlists the router.

---

### Recommendation

Pass the original user's address through the swap path so the extension can check it. One approach: add a `swapper` field to `extensionData` that the router populates with `msg.sender` before calling `pool.swap()`, and have the extension decode and verify it. A cleaner approach is to have the pool accept an explicit `swapper` parameter (separate from `recipient`) and forward it to extensions, so the extension can check `allowedSwapper[pool][swapper]` against the true economic actor regardless of which intermediary called the pool.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin allowlists only alice:
       swapExtension.setAllowedToSwap(pool, alice, true)
3. Pool admin also allowlists the router so alice can use it:
       swapExtension.setAllowedToSwap(pool, router, true)
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
5. Router calls pool.swap(bob_recipient, ...) — msg.sender to pool = router.
6. Extension checks allowedSwapper[pool][router] == true → passes.
7. Bob's swap executes on the curated pool despite never being allowlisted.
```

The root cause is at: [5](#0-4) 

where `sender` is the router address, not the actual user, whenever the swap is routed through `MetricOmmSimpleRouter`.

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
