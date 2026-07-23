### Title
`SwapAllowlistExtension` Checks Direct Caller (`sender`) Instead of Original User, Allowing Any User to Bypass the Swap Allowlist via the Router - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is the address that called `pool.swap()` directly. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the original user. If the pool admin allowlists the router (which is necessary for any allowlisted user to trade via the router), the allowlist is silently bypassed for every user — allowlisted or not.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (enforced by `onlyPool`). `sender` is whatever the pool passed as the first argument to `_beforeSwap`, which is always `msg.sender` of the pool's `swap()` call:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the original user
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

The pool's `msg.sender` is the **router address**. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`.

For allowlisted users to trade via the router at all, the pool admin must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every caller — allowlisted or not — because the extension has no visibility into who called the router.

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` paths in the router.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps against the pool, extracting value at oracle-anchored prices that the pool admin intended to offer only to approved parties. This is a direct loss of the curation guarantee and can result in unauthorized fund flows out of the pool.

---

### Likelihood Explanation

The bypass requires the router to be allowlisted. This is a realistic and expected operational step: without it, even allowlisted users cannot use the router, making the router useless for curated pools. The protocol provides no warning that allowlisting the router opens the pool to all users. Any pool admin who enables router-based trading for their allowlisted users will inadvertently enable it for everyone.

---

### Recommendation

The extension must verify the original user, not the intermediary. Two approaches:

1. **Pass the original user through the router**: Modify `MetricOmmSimpleRouter` to encode `msg.sender` in `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it. This requires a trust assumption that the extension only accepts this encoding from a known router.

2. **Check `sender` and fall back to a router-origin check**: If `sender` is a known router, require the extension payload to carry the original user's address and a signature or transient-storage proof. This is the more robust approach.

The simplest safe fix is to document that the router must never be allowlisted and that curated pools must be accessed only via direct `pool.swap()` calls — but this severely limits usability and is not a code-level fix.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully trades on a pool he was never authorized to access. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
