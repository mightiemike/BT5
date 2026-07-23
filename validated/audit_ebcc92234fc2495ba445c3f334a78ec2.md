### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter — Any Unprivileged User Can Swap on Allowlist-Gated Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument the pool passes in. When `MetricOmmSimpleRouter` is the caller of `pool.swap()`, `sender` resolves to the router's address, not the end user's address. A pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously opens the gate to every unprivileged user who routes through the same public router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` seen by the pool is the **router address**, so the extension's check becomes `allowedSwapper[pool][router]`. For router-mediated swaps to work at all for any allowlisted user, the pool admin must add the router to the allowlist. Once the router is allowlisted, the check passes for every caller of the router regardless of their individual allowlist status, because the extension never sees the original end-user address.

The extension's `allowedSwapper` and `allowAllSwappers` mappings are keyed by pool and swapper: [5](#0-4) 

There is no mechanism in the extension, the pool, or the router to propagate the original `msg.sender` of the router call into the `sender` slot seen by the extension.

### Impact Explanation

Any unprivileged user can bypass a pool's configured swap allowlist by routing through `MetricOmmSimpleRouter`. The pool admin faces an impossible choice: either allowlist the router (opening the gate to all users) or do not allowlist it (breaking router-mediated swaps for every legitimately allowlisted user). This is an admin-boundary break where an unprivileged path bypasses a configured access-control guard. Pools that use the allowlist to restrict trading to specific counterparties (e.g., regulated venues, whitelisted market makers, or KYC-gated LPs) will have that restriction silently nullified for all router-originated swaps.

### Likelihood Explanation

Likelihood is medium-high. The `SwapAllowlistExtension` is a production periphery contract explicitly documented as a swap gate. Any operator who deploys it and also wants users to access the pool through the standard router will allowlist the router as a routine operational step. The bypass requires no special privilege, no flash loan, and no oracle manipulation — only a call to a public router function.

### Recommendation

The `sender` identity passed through the hook chain must reflect the economic actor, not the intermediary contract. Two viable approaches:

1. **Router-side**: Have the router encode the original `msg.sender` into `extensionData` and have the extension decode and check it instead of (or in addition to) the `sender` argument.
2. **Extension-side**: The extension should treat the router as a transparent forwarder and require the router to attest the real caller via a signed or transient-storage mechanism, similar to how the router already stores the payer in transient storage for the callback.

Until fixed, pool admins should not simultaneously deploy `SwapAllowlistExtension` and allowlist `MetricOmmSimpleRouter`.

### Proof of Concept

```
// Setup
pool admin deploys pool with SwapAllowlistExtension configured
pool admin calls: extension.setAllowedToSwap(pool, address(router), true)
  // router allowlisted so legitimate users can swap via router
pool admin does NOT call: extension.setAllowedToSwap(pool, address(attacker), true)
  // attacker is explicitly not allowlisted

// Attack
vm.prank(attacker);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    ...
}));
// router calls pool.swap(...) → msg.sender seen by pool = router
// pool calls extension.beforeSwap(router, ...) 
// extension checks: allowedSwapper[pool][router] == true  ✓
// swap executes — attacker bypasses the allowlist entirely
```

The extension's `isAllowedToSwap` view function confirms the asymmetry: `isAllowedToSwap(pool, attacker)` returns `false`, yet the attacker's swap succeeds because the on-chain check evaluates `isAllowedToSwap(pool, router)`. [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L27-41)
```text
  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
