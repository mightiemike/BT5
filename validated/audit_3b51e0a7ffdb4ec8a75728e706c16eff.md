### Title
SwapAllowlistExtension Gates on Router Address Instead of Actual User, Allowing Any User to Bypass Curated Pool Restrictions via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` equals the router address, not the actual user. A pool admin who allowlists the router (a necessary step to let their allowlisted users trade conveniently) inadvertently grants every user on-chain access to the curated pool.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap()
  recipient,
  ...
  extensionData
);
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension = the pool):

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

The pool's `msg.sender` is the router. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The actual user's identity is stored only in transient storage for the payment callback and is never surfaced to the extension.

For a curated pool to be usable by its allowlisted users through the router, the admin **must** add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every caller, regardless of who the actual user is. The per-user allowlist is completely bypassed.

---

### Impact Explanation

Any user can trade on a curated pool (e.g., KYC-gated, institution-only, or regulatory-restricted) by routing through the public `MetricOmmSimpleRouter`. The allowlist invariant — "only approved addresses may swap" — is broken. Depending on the pool's purpose, this can mean:

- Unauthorized users extract liquidity at oracle-anchored prices from a pool intended for a closed set of counterparties.
- Regulatory or compliance guarantees of the pool operator are violated.
- LP funds are exposed to counterparties the pool was explicitly designed to exclude.

This is a direct loss-of-policy impact with fund-level consequences for LPs in curated pools.

---

### Likelihood Explanation

The bypass requires the router to be allowlisted. However, this is not an edge case — it is the **only** way allowlisted users can use the router on a curated pool. Any operator who deploys a curated pool and wants their approved users to access it through the standard periphery must allowlist the router. The configuration that triggers the vulnerability is the expected production configuration for curated pools with router support.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the actual economic actor, not the immediate caller of `pool.swap()`. Two sound approaches:

1. **Router-forwarded identity via `extensionData`**: Require the router to ABI-encode the actual user's address into `extensionData` and have the extension decode and check it. The extension should reject calls where `sender` is the router but no valid user identity is present in `extensionData`.

2. **Separate allowlist entry for routed vs. direct swaps**: Document clearly that allowlisting the router opens the pool to all users, and provide a separate extension variant that decodes user identity from `extensionData` for router-mediated flows.

Using `tx.origin` is not recommended as it breaks contract-to-contract composability.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is approved.
3. Admin calls `setAllowedToSwap(pool, router, true)` — router is approved so Alice can use it.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully trades on the curated pool despite never being allowlisted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-85)
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
