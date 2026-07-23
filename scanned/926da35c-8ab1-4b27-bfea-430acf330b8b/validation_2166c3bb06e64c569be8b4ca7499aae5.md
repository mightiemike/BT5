### Title
SwapAllowlistExtension Checks Router Address as `sender` Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual end user. If the pool admin allowlists the router (which is required for any router-mediated swap to succeed), every user — including those the allowlist was designed to exclude — can bypass the gate by routing through the router.

---

### Finding Description

**Call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle(...)
         → pool.swap(recipient, ...) [msg.sender = router]
             → ExtensionCalling._beforeSwap(sender=router, ...)
                 → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                     → checks allowedSwapper[pool][router]
```

In `MetricOmmSimpleRouter.exactInputSingle`, the pool's `swap` is called directly with `msg.sender = router`: [1](#0-0) 

The pool passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that `sender` value unchanged to every configured extension: [3](#0-2) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [4](#0-3) 

The pool admin faces an inescapable dilemma:

- **Do not allowlist the router** → all router-mediated swaps revert for every user, including allowlisted ones. The router becomes unusable on this pool.
- **Allowlist the router** → the check becomes `allowedSwapper[pool][router] == true`, which passes for every caller regardless of who the actual end user is. The per-user allowlist is completely bypassed.

There is no configuration that simultaneously allows allowlisted users to swap through the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to a known set of counterparties (e.g., a KYC'd whitelist or a partner set). Any non-allowlisted user can bypass this restriction by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The router is a public, permissionless contract. The bypass requires no special privileges, no flash loan, and no multi-step setup. The result is unauthorized swaps against LP capital, constituting a direct loss of LP assets above Sherlock thresholds.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard user-facing entry point for the protocol. Pool admins who want their allowlisted users to be able to use the router (the normal UX) must allowlist the router address, which immediately opens the gate to all users. The vulnerability is triggered by any ordinary swap through the router on an allowlisted pool.

---

### Recommendation

Pass the original end-user address through the extension system rather than the immediate `msg.sender` of the pool call. Two concrete approaches:

1. **Router forwards the real user in `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData` before calling the pool. `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of `sender`. This requires a convention between the router and the extension.

2. **Pool exposes an `originator` field**: Add an optional `originator` parameter to `swap` (defaulting to `msg.sender` when called directly) that the router populates with its own `msg.sender`. Extensions receive `originator` as the identity to gate.

Either approach must ensure the router cannot be used to spoof an allowlisted address.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted.
// pool admin allowlists alice:
swapAllowlist.setAllowedToSwap(address(pool), alice, true);
// pool admin also allowlists the router so alice can use it:
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// Attack: bob (not allowlisted) swaps through the router.
vm.startPrank(bob);
token1.approve(address(router), type(uint256).max);
// This succeeds because the extension sees sender=router, which IS allowlisted.
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token1),
        recipient: bob,
        zeroForOne: false,
        amountIn: 1_000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp + 1,
        extensionData: ""
    })
);
// bob successfully swapped on a pool he was never allowlisted for.
```

The same bypass applies to `exactInput` (multi-hop) and `exactOutput` / `exactOutputSingle` paths, since all of them call `pool.swap` with `msg.sender = router`. [5](#0-4) [4](#0-3)

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
