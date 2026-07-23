### Title
`SwapAllowlistExtension` Bypass via `MetricOmmSimpleRouter` — Router Address Replaces User Identity in Allowlist Check - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. The pool passes `msg.sender` of `pool.swap()` as `sender`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` inside `pool.swap()` is the router contract, not the original EOA. If the pool admin allowlists the router address to enable router-mediated swaps for legitimate users, every unprivileged user can bypass the allowlist entirely by routing through the same public router.

### Finding Description

**Call chain for a direct swap:**
```
EOA (alice) → pool.swap() → _beforeSwap(msg.sender=alice, ...) 
  → SwapAllowlistExtension.beforeSwap(sender=alice, ...)
  → checks allowedSwapper[pool][alice]  ✓ or ✗
```

**Call chain for a router-mediated swap:**
```
EOA (attacker) → MetricOmmSimpleRouter.exactInputSingle()
  → pool.swap()  [msg.sender = router]
  → _beforeSwap(msg.sender=router, ...)
  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
  → checks allowedSwapper[pool][router]
```

In `MetricOmmPool.swap()`, the `sender` forwarded to `_beforeSwap` is always `msg.sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` passes that `sender` verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][router]` — not the original EOA: [3](#0-2) 

The router calls `pool.swap()` directly with no mechanism to thread the original user's address through as `sender`: [4](#0-3) 

This creates an irreconcilable conflict for the pool admin:

- **If the router is NOT allowlisted**: allowlisted users cannot use the router at all (their direct-pool allowance does not transfer to router-mediated calls).
- **If the router IS allowlisted** (the natural fix to let allowlisted users use the router): `allowedSwapper[pool][router] = true` passes for every caller of the router, so any unprivileged user bypasses the allowlist by routing through the public `MetricOmmSimpleRouter`.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties (e.g., KYC'd users, specific market makers, or whitelisted protocols) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The attacker executes real swaps against the pool's liquidity, receiving output tokens and paying input tokens at oracle-derived prices. LP funds are consumed by unauthorized counterparties, violating the pool admin's curation policy and potentially exposing LPs to adversarial flow they explicitly opted out of.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Pool admins who deploy a `SwapAllowlistExtension` and want their allowlisted users to have a normal UX will naturally allowlist the router. The bypass requires no special privileges, no flash loans, and no multi-step setup — any EOA can call `exactInputSingle` on the router pointing at the restricted pool.

### Recommendation

The `SwapAllowlistExtension` must gate the economically relevant actor, not the immediate caller. Two viable fixes:

1. **Check `sender` against the allowlist but also accept the router as a transparent forwarder**: require the router to pass the original EOA in `extensionData`, and have the extension decode and check that address when `sender` is a known router. This requires a trusted-router registry in the extension.

2. **Preferred — check `sender` only, and require allowlisted users to call `pool.swap()` directly**: document clearly that the allowlist is incompatible with router-mediated swaps, and do not allowlist the router. Allowlisted users must interact with the pool directly or through a custom wrapper that is itself allowlisted and enforces per-user identity.

A third option is to redesign the extension to accept a signed proof of the original EOA in `extensionData`, verified against a trusted signer, so the router can forward the user's identity without the extension trusting the router blindly.

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension, only alice is allowlisted
swapExtension.setAllowedToSwap(address(pool), alice, true);
// Admin allowlists the router so alice can use it
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not allowlisted) bypasses the gate via the router
vm.startPrank(attacker);
token0.approve(address(router), type(uint256).max);
// This succeeds: allowedSwapper[pool][router] == true
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: address(token0),
    deadline: block.timestamp,
    extensionData: ""
}));
// Attacker receives token1 from the restricted pool — allowlist bypassed
```

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
