### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is always `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the **router contract**, not the actual user. An admin who allowlists the router to enable router-based swaps for their permitted users inadvertently opens the pool to every user on the router, completely defeating the per-user gate.

### Finding Description

**Call chain when a user swaps through the router:**

```
user → MetricOmmSimpleRouter.exactInputSingle(...)
         → pool.swap(recipient, zeroForOne, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

`MetricOmmPool.swap` always passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the **router address**. The extension therefore checks whether the router is allowlisted, not whether the actual end-user is allowlisted.

**Bypass scenario:**

1. Admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to KYC'd addresses.
2. Admin allowlists user A: `setAllowedToSwap(pool, userA, true)`.
3. Admin also allowlists the router so that user A can use the router: `setAllowedToSwap(pool, router, true)`.
4. Unapproved user B calls `router.exactInputSingle(...)`. The extension sees `sender = router`, finds `allowedSwapper[pool][router] = true`, and passes. User B swaps successfully despite never being allowlisted.

Step 3 is the natural, reasonable admin action: without it, even allowlisted users cannot use the router. Yet it silently opens the pool to all router users.

There is no mechanism in the current design to allowlist specific users *for router-based swaps*; the only choices are "allowlist the router (all users pass)" or "don't allowlist the router (no router user passes)."

### Impact Explanation

Any user can bypass the swap allowlist by routing through `MetricOmmSimpleRouter` once the admin has allowlisted the router. The allowlist is the sole access-control layer for pools that require it (e.g., permissioned institutional pools, KYC-gated pools). A bypass allows unauthorized parties to execute swaps, draining pool liquidity at oracle-anchored prices and causing direct loss of LP principal.

### Likelihood Explanation

The admin action that triggers the bypass (allowlisting the router) is the natural, expected step to enable router-based swaps for permitted users. The documentation and interface give no indication that doing so opens the pool to all users. Any pool operator who deploys a `SwapAllowlistExtension` and also wants router support will hit this path. The bypass itself requires no special privileges or unusual conditions once the router is allowlisted.

### Recommendation

The extension must gate the **economically relevant actor** — the end-user — not the intermediary router. Two viable approaches:

1. **Router passes the real user in `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData` for each hop, and `SwapAllowlistExtension` decodes and checks that address instead of (or in addition to) `sender`.

2. **Check `sender` only for direct calls; require `extensionData` attestation for router calls**: The extension can detect that `sender` is a known router and require a signed or encoded user identity in `extensionData`.

Either way, the extension must not treat the router address as the identity to gate.

### Proof of Concept

```solidity
// 1. Pool admin sets up allowlist for userA only
swapAllowlist.setAllowedToSwap(pool, userA, true);

// 2. Admin allowlists the router so userA can use it
swapAllowlist.setAllowedToSwap(pool, address(router), true);

// 3. userB (NOT allowlisted) calls the router
vm.prank(userB);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        tokenIn: token0,
        tokenOut: token1,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: userB,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// ↑ succeeds — userB bypassed the allowlist
// Extension saw sender=router, allowedSwapper[pool][router]=true → no revert
```

**Relevant code locations:**

- `SwapAllowlistExtension.beforeSwap` checks `sender` (the router) instead of the actual user: [1](#0-0) 
- `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [2](#0-1) 
- `ExtensionCalling._beforeSwap` forwards `sender` unchanged to the extension: [3](#0-2) 
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` as `msg.sender` (the router): [4](#0-3)

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
