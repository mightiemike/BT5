### Title
SwapAllowlistExtension Gates the Router Address Instead of the Original User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool sees the router as `msg.sender`, so the extension checks whether the **router** is allowlisted, not whether the **original user** is allowlisted. If a pool admin allowlists the router address to enable router-mediated swaps for their curated users, every unprivileged user can bypass the allowlist by calling through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 231
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
// ExtensionCalling.sol line 163
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
```

`SwapAllowlistExtension.beforeSwap` then gates on that forwarded `sender`:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

The pool's `msg.sender` is now the router, so the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][original_user]`.

A pool admin who wants their allowlisted users to be able to use the router (for multi-hop swaps, ETH wrapping, slippage protection, etc.) must add the router to the allowlist. Once the router is allowlisted, **any** user can call `router.exactInputSingle()` and the extension passes unconditionally, because `allowedSwapper[pool][router] == true`.

The original user's address is stored in transient storage as the payer for the callback (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)`), but this value is never surfaced to the extension.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd, institutional, or otherwise vetted counterparties is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. The attacker receives pool output tokens at oracle-derived prices without being on the allowlist. This is a direct loss of the pool's curation guarantee and, depending on pool design, can result in LP value leakage to unintended counterparties.

---

### Likelihood Explanation

The bypass requires the pool admin to have allowlisted the router address. This is a natural and expected action: any pool admin who wants their allowlisted users to benefit from multi-hop routing, ETH wrapping, or slippage-protected exact-output swaps must allowlist the router. The admin's mental model ("I allowlisted the router so my vetted users can use it") does not match the actual behavior ("anyone can now use the router on this pool"). The trigger is a valid, semi-trusted admin action with a predictable misunderstanding of the actor-binding semantics.

---

### Recommendation

The extension must check the **original user**, not the direct pool caller. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Check `sender` only when `sender` is not a known router**: The extension maintains a registry of trusted routers and, when `sender` is a router, requires the extension data to carry the verified original user address.

The deposit-side extension (`DepositAllowlistExtension`) correctly gates on `owner` (the position owner), which is preserved end-to-end through the liquidity adder. The swap-side extension should adopt the same pattern by gating on the economically relevant actor rather than the transport-layer caller.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for their vetted users.
3. Non-allowlisted attacker calls:
   ```solidity
   router.exactInputSingle(ExactInputSingleParams({
       pool: curatedPool,
       tokenIn: token0,
       tokenOut: token1,
       zeroForOne: true,
       amountIn: 10_000,
       amountOutMinimum: 0,
       recipient: attacker,
       deadline: block.timestamp + 1,
       priceLimitX64: 0,
       extensionData: ""
   }));
   ```
4. The pool calls `extension.beforeSwap(router, ...)`. The extension checks `allowedSwapper[pool][router] == true` and passes. The attacker receives token1 output despite never being on the allowlist.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
