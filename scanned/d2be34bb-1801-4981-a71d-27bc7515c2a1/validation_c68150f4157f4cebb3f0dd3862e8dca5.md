### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual user. If the pool admin allowlists the router (a natural action to enable router-based swaps), every user — including non-allowlisted ones — bypasses the per-user restriction.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap(...)` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The actual end-user (`msg.sender` of the router call) is never forwarded to the pool or the extension. The extension has no way to observe it.

**Bypass path:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC-verified users).
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps for allowlisted users — a natural configuration step.
3. Any non-allowlisted user calls `router.exactInputSingle(...)`. The pool receives `msg.sender = router`. The extension evaluates `allowedSwapper[pool][router] == true` and passes. The swap executes.
4. The per-user allowlist is completely bypassed.

The `isAllowedToSwap` view function compounds the confusion: a pool admin calling `isAllowedToSwap(pool, alice)` returns `false` for a non-allowlisted user, giving no indication that `alice` can still swap freely through the router. [5](#0-4) 

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to enforce a curated or permissioned swap policy (KYC, institutional-only, whitelist-gated liquidity) is rendered ineffective for all router-based swaps once the router is allowlisted. Any unprivileged user can trade in the pool by routing through `MetricOmmSimpleRouter`, defeating the curation invariant. This constitutes a broken core pool functionality and an admin-boundary break where an unprivileged path bypasses a configured guard.

### Likelihood Explanation

The router is the primary supported swap entrypoint in the periphery. A pool admin who wants to allow router-based swaps for their allowlisted users will naturally call `setAllowedToSwap(pool, router, true)`. Nothing in the extension interface, documentation, or error messages warns that this action opens the pool to all users. The misconfiguration is easy to make and hard to detect after the fact.

### Recommendation

The extension must gate by the actual initiating user, not the direct caller of the pool. Two approaches:

1. **Pass `tx.origin` or a user-supplied identity through `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData` before calling `pool.swap`. The extension reads and verifies that value. This requires a coordinated change to the router and extension.

2. **Reject non-EOA senders at the extension level**: If `sender` is a contract (e.g., the router), revert unless `allowAllSwappers[pool]` is set. This forces direct EOA calls for allowlisted pools and prevents router-based bypass.

3. **Document the limitation explicitly**: If the design intent is that `sender` is the direct pool caller, document that allowlisting the router grants access to all users and that per-user router-level gating is not supported by this extension.

### Proof of Concept

```solidity
// Pool configured with SwapAllowlistExtension; only `alice` is allowlisted.
// Pool admin also allowlists the router to enable router-based swaps.
// Result: `charlie` (not allowlisted) swaps successfully through the router.

function testSwapAllowlistBypassViaRouter() public {
    // Setup: deploy pool with SwapAllowlistExtension
    SwapAllowlistExtension ext = new SwapAllowlistExtension(address(factory));
    address pool = _deployPoolWithExtension(address(ext));

    // Admin allowlists alice and the router
    vm.prank(poolAdmin);
    ext.setAllowedToSwap(pool, alice, true);
    vm.prank(poolAdmin);
    ext.setAllowedToSwap(pool, address(router), true); // natural: enable router swaps

    // charlie is NOT allowlisted
    assertFalse(ext.isAllowedToSwap(pool, charlie));

    // charlie swaps through the router — extension sees sender=router, passes
    vm.prank(charlie);
    router.exactInputSingle(ExactInputSingleParams({
        pool: pool,
        recipient: charlie,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    }));
    // charlie successfully swapped despite not being allowlisted
}
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L27-29)
```text
  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
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
