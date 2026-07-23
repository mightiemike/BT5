### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. The pool always passes its own `msg.sender` as `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router to enable router-based swaps for their curated users, every non-allowlisted address can bypass the per-user gate by routing through the router.

### Finding Description

**Call chain when a user swaps via the router:**

```
userB (not allowlisted)
  → MetricOmmSimpleRouter.exactInputSingle(params)
      → pool.swap(recipient, ...)          // msg.sender = router
          → _beforeSwap(msg.sender=router, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                  checks: allowedSwapper[pool][router]  ← router IS allowlisted
                  → passes
```

`SwapAllowlistExtension.beforeSwap` at line 37:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct). `sender` is the parameter forwarded by the pool from its own `msg.sender`, which is the router when the call originates from `MetricOmmSimpleRouter`. [1](#0-0) 

The pool unconditionally passes `msg.sender` as `sender` to `_beforeSwap`: [2](#0-1) 

The router calls `pool.swap(...)` without forwarding the original caller's identity: [3](#0-2) 

This creates an irresolvable dilemma for the pool admin:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all (broken UX) |
| Allowlist the router | **Every** address can bypass the per-user allowlist via the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

### Impact Explanation

A pool deployer who configures `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or protocol-controlled addresses) cannot enforce that restriction when the router is in use. Any non-allowlisted address can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and trade against the pool as if the allowlist did not exist. This is a direct policy bypass on a curated pool, which the impact gate classifies as High. [4](#0-3) 

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point documented and shipped with the protocol. Any pool admin who wants their allowlisted users to interact via the standard router must allowlist the router address. The moment they do, the allowlist is effectively open to all. The trigger is a routine, non-malicious admin action, not an exotic attack setup. [5](#0-4) 

### Recommendation

The extension must gate the **economic actor** (the end user), not the intermediary. Two viable approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The `SwapAllowlistExtension` decodes and verifies it (requires a signature or trusted-router pattern to prevent spoofing).

2. **Add an `originator` field to the hook signature**: Extend `IMetricOmmExtensions.beforeSwap` with an `originator` parameter that the pool populates from a transient-storage slot set by the router before calling `pool.swap`. The extension checks `originator` instead of `sender`.

Either approach must be resistant to a non-allowlisted user crafting `extensionData` to impersonate an allowlisted address.

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
swapAllowlist.setAllowedToSwap(address(pool), allowedUser, true);
// Admin allowlists the router so allowedUser can use it.
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// Attack: non-allowlisted attacker routes through the router.
vm.prank(attacker); // attacker is NOT in allowedSwapper[pool]
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token0),
        tokenOut:        address(token1),
        zeroForOne:      true,
        amountIn:        1_000,
        amountOutMinimum: 0,
        recipient:       attacker,
        deadline:        block.timestamp + 1,
        priceLimitX64:   0,
        extensionData:   ""
    })
);
// Swap succeeds: extension checked allowedSwapper[pool][router] == true,
// never checking whether `attacker` is allowlisted.
``` [6](#0-5) [2](#0-1) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-42)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

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
