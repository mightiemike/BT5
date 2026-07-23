### Title
SwapAllowlistExtension checks router address as `sender` instead of the actual end-user, allowing any user to bypass the swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual end-user. The allowlist therefore checks the router's address, not the user's address. If the pool admin allowlists the router (the only way to permit router-mediated swaps), every user — including those the admin explicitly excluded — can bypass the allowlist and swap on the restricted pool.

---

### Finding Description

**Allowlist check identity mismatch**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol  line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    zeroForOne,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol  line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)   // sender = msg.sender of pool.swap()
    )
);
```

`SwapAllowlistExtension.beforeSwap` then gates on that `sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

At this point `msg.sender` to the pool is the **router address**. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**Bypass path**

The pool admin must allowlist the router address to permit any router-mediated swap. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every caller of the router, regardless of whether that caller is individually permitted. The per-user allowlist is completely neutralised for the router path.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of users (e.g., KYC-verified counterparties, institutional market makers, or whitelisted protocols) is fully bypassed by any unprivileged user who routes through `MetricOmmSimpleRouter`. The attacker can:

1. Execute swaps on a pool that was designed to be closed to them.
2. Drain LP-owned liquidity at oracle prices in directions the stop-loss or velocity guard would otherwise have blocked (because those guards run *after* the allowlist passes).
3. Interact with pools whose LP terms (fees, bin configuration) were negotiated exclusively for the allowlisted counterparties, extracting value at rates the LPs did not intend to offer to the general public.

This constitutes broken core pool functionality and an admin-boundary break reachable by an unprivileged path.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is a public, permissionless contract.
- Any user can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` targeting any pool address.
- No factory validation prevents a non-allowlisted user from routing through the router to a restricted pool.
- The pool admin has no on-chain mechanism to distinguish "router called by an allowlisted user" from "router called by anyone else" under the current design.
- Likelihood is **high** whenever a pool is deployed with `SwapAllowlistExtension` and the router is allowlisted.

---

### Recommendation

Gate on the **economic actor** (the end-user), not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the router.** The router should forward `msg.sender` as an authenticated field inside `extensionData` (signed or verified via transient storage), and the extension should decode and check that field instead of the raw `sender` argument.

2. **Alternatively, check `recipient` or require direct pool calls for allowlisted pools.** Document clearly that `SwapAllowlistExtension` is incompatible with router-mediated swaps unless the router itself is the intended gate.

3. **Short-term mitigation:** Do not allowlist the router address on pools that use `SwapAllowlistExtension` for per-user access control. Require allowlisted users to call `pool.swap()` directly.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: restrictedPool, ...})
  - Router calls restrictedPool.swap(recipient, ...) → msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true  ✓
  - Swap executes; attacker receives output tokens from the restricted pool.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
