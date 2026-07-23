### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` resolves to the router contract address, not the end user. If the pool admin allowlists the router (a natural action to enable router-mediated swaps for their allowlisted users), every unprivileged address can bypass the allowlist by routing through the router.

### Finding Description

**Hook argument binding — `ExtensionCalling._beforeSwap`:**

The pool passes `msg.sender` of `pool.swap()` as the first argument to every before-swap extension: [1](#0-0) [2](#0-1) 

**Router call — `MetricOmmSimpleRouter.exactInputSingle`:**

The router calls `pool.swap()` directly, so `msg.sender` inside the pool is the router contract, not the end user: [3](#0-2) 

**Guard check — `SwapAllowlistExtension.beforeSwap`:**

The extension receives `sender = router` and evaluates `allowedSwapper[pool][router]`: [4](#0-3) 

**Attack path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists specific KYC'd users via `setAllowedToSwap(pool, user_A, true)`.
2. Pool admin also calls `setAllowedToSwap(pool, router, true)` so that allowlisted users can swap through the router (a natural, non-malicious action).
3. Attacker (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(...)` — `msg.sender` inside the pool is the router.
5. Pool dispatches `_beforeSwap(sender=router, ...)` to the extension.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → passes.
7. Attacker completes the swap in the restricted pool.

The invariant `allowedSwapper[pool][actual_end_user]` is never consulted on the router path. The guard is structurally misbound: it checks the intermediary, not the economically relevant actor.

**Structural consequence without the router allowlisted:**

If the admin does not allowlist the router, allowlisted users cannot use the router at all — the extension reverts on `allowedSwapper[pool][router] == false`. The extension therefore forces a binary choice: either the router is allowlisted (bypass) or the router is unusable for the pool (broken functionality).

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a specific set of addresses (e.g., KYC-gated, institutional-only) is fully open to any caller who routes through `MetricOmmSimpleRouter` once the router is allowlisted. Unauthorized swaps drain pool liquidity and violate the access-control invariant the pool admin intended to enforce.

### Likelihood Explanation

The trigger is a non-malicious, expected pool-admin action: allowlisting the router so that allowlisted users can benefit from multi-hop routing. The admin has no on-chain signal that this action opens the pool to all users. Any pool that combines `SwapAllowlistExtension` with router support is affected.

### Recommendation

The extension must gate on the actual end user, not the direct caller. Two options:

1. **Pass the real user through `extensionData`**: Have the router encode `msg.sender` (the end user) into `extensionData` and have the extension decode and check it. This requires a coordinated convention between router and extension.
2. **Check `sender` only when `sender` is not a known router**: Maintain a registry of trusted routers in the extension; when `sender` is a trusted router, decode the real user from `extensionData`; otherwise check `sender` directly.

The simplest safe fix is to remove router support from the allowlist model entirely and require end users to call `pool.swap()` directly when the allowlist extension is active.

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Admin allowlists router so allowlisted users can use it
vm.prank(poolAdmin);
ext.setAllowedToSwap(pool, address(router), true);

// Attacker (not individually allowlisted) routes through the router
vm.prank(attacker); // attacker is NOT in allowedSwapper[pool]
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: token0,
    recipient: attacker,
    amountIn: 1e18,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Swap succeeds — allowlist bypassed
// allowedSwapper[pool][router] == true was checked, not allowedSwapper[pool][attacker]
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
