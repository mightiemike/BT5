### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the value forwarded by the pool as the first argument. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so `sender` is the router address — not the end user. Any user who routes through the router is checked against the router's allowlist entry, not their own. If the pool admin allowlists the router (required for any router-mediated swap to succeed), the allowlist is completely bypassed for all users.

---

### Finding Description

**Root cause — wrong actor forwarded to the guard:**

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` then ABI-encodes that value as the first positional argument and dispatches it to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` receives that value as `sender` and checks it against the per-pool allowlist: [3](#0-2) 

When the user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is `msg.sender` of the pool's `swap` call: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]` — the router's allowlist entry — instead of `allowedSwapper[pool][end_user]`.

**The inescapable dilemma for the pool admin:**

| Admin choice | Effect |
|---|---|
| Allowlist the router | Every user bypasses the allowlist through the router |
| Do not allowlist the router | No user — including allowlisted ones — can swap through the router |

There is no configuration that simultaneously permits router-mediated swaps for allowlisted users while blocking non-allowlisted users.

**Analog to the ERC20Wrapper `checkSuccess` pattern:**

The external report describes a validation function (`checkSuccess`) that is sensitive to execution context — it must be called immediately after the right external call or it validates the wrong return value. The analog here is `beforeSwap`: the guard is sensitive to the execution path. When the path goes through the router, the guard validates the wrong actor (the router instead of the end user), producing an incorrect authorization decision — exactly the same class of context-sensitive validation failure.

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to KYC-verified or otherwise approved counterparties. Any non-approved user can bypass this restriction by routing through `MetricOmmSimpleRouter`. If the pool offers favorable pricing, subsidized rates, or is part of a compliance-gated venue, unauthorized users can extract value from the pool or violate the curation policy, causing direct LP losses or regulatory exposure. This is a broken core pool functionality with direct fund-impacting consequences.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported user-facing entry point for swaps. Any pool admin who deploys a `SwapAllowlistExtension`-gated pool and wants legitimate allowlisted users to use the router must allowlist the router address. Once the router is allowlisted, the bypass is trivially reachable by any user with no special privileges, no malicious setup, and no non-standard tokens.

---

### Recommendation

The `beforeSwap` hook must receive and check the **original end user's address**, not the intermediary's address. Two approaches:

1. **Short term:** The pool should forward the original initiator rather than `msg.sender`. One pattern is to have the router pass the end user's address inside `extensionData`, and have the extension decode and verify it — but this requires the extension to trust the router's encoding, which introduces its own risks.

2. **Long term:** Redesign the extension interface so that the pool passes both `msg.sender` (the direct caller) and an authenticated `originator` (the end user, verified via a signed permit or transient-storage context set by the router before the pool call). The allowlist guard should check `originator`, not `sender`. This mirrors the correct pattern: every guard must key authorization to the same actor that the economic action is actually attributed to.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin: setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  - Pool admin: setAllowedToSwap(pool, router, true)  // required for router-mediated swaps
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient=bob, ...) — pool's msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] == true  ✓
  5. Swap executes — bob receives tokens from the curated pool
  6. Allowlist is bypassed with zero special privileges
```

The invariant `allowedSwapper[pool][bob] == false` is never evaluated; the guard consumed the router's allowlist entry instead of bob's.

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
