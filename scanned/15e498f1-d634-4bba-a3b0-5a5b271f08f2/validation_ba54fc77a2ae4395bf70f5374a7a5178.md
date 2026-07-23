The call chain is fully traceable and the vulnerability is real. Here is the complete analysis:

---

### Title
Swap Allowlist Bypass via Router Intermediary — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` mediates a swap, `msg.sender` at the pool is the **router address**, not the end user. Any pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the pool to every user, because the extension has no way to distinguish which EOA initiated the router call.

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension:**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

**Step 2 — Extension checks `allowedSwapper[pool][sender]` where `sender` is the pool's `msg.sender`:** [2](#0-1) 

**Step 3 — Router calls `pool.swap()` directly, making itself `msg.sender` at the pool:** [3](#0-2) 

The router stores the original `msg.sender` only in transient callback context for payment purposes (`_setNextCallbackContext(..., msg.sender, ...)`), but **never forwards it to the pool as the swap `sender`**. The pool always sees `router` as `msg.sender`.

**Resulting invariant break:**

| Configuration | Direct EOA swap | Router-mediated swap |
|---|---|---|
| `allowedSwapper[pool][eoa] = true` | ✅ allowed | ❌ blocked (router not listed) |
| `allowedSwapper[pool][router] = true` | ❌ blocked (EOA not listed) | ✅ **any user bypasses** |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users. Allowlisting the router is the only way to enable router-mediated swaps, and doing so grants access to every user.

### Impact Explanation

Any unprivileged EOA can swap on a pool configured with `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter` if the router address is in the allowlist. The intended access control (e.g., KYC gating, whitelist-only pools) is completely bypassed. This constitutes broken core pool functionality — the allowlist extension fails to gate the actual economic actor.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical swap periphery. Pool admins who want allowlisted users to be able to use the router will naturally add the router to the allowlist. The bypass is then available to every user with no special privileges or knowledge beyond knowing the router address.

### Recommendation

The extension must receive the original initiating address, not the intermediary. Two options:

1. **Pass original sender via `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a protocol-level convention.
2. **Check `recipient` instead of (or in addition to) `sender`:** For single-hop swaps the recipient is often the end user, though this is not reliable for multi-hop paths.
3. **Preferred — dedicated `originalSender` field in the hook interface:** Add an `originalSender` parameter to `beforeSwap` that the pool populates from a trusted periphery-provided field, similar to how Uniswap v4 uses `hookData` for this pattern.

### Proof of Concept

```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. Admin allowlists the router: extension.setAllowedToSwap(pool, address(router), true)
// 3. Unlisted EOA calls router.exactInputSingle(...)
// 4. pool.swap() is called with msg.sender = router
// 5. beforeSwap checks allowedSwapper[pool][router] = true → passes
// 6. Unlisted EOA's swap executes successfully — allowlist bypassed
``` [4](#0-3) [5](#0-4)

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
