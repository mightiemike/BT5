### Title
`SwapAllowlistExtension` gates the router address instead of the end user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which is the **immediate caller of the pool** (`msg.sender` inside `MetricOmmPool.swap`). When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router to enable router-mediated swaps for allowlisted users, every non-allowlisted user can bypass the per-user allowlist by routing through the same public router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle(...)
         → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
              → MetricOmmPool._beforeSwap(msg.sender, ...)    // sender = router
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]         // checks router, not user
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

When the user calls through `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of the pool: [4](#0-3) 

This creates an irresolvable tension for the pool admin:

| Router allowlisted? | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| No | ❌ Blocked | ✓ Blocked |
| Yes | ✓ Allowed | ❌ **Also allowed — bypass** |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from using the router. The protocol's own audit target confirms the intended invariant: *"the hook must gate the same actor the pool designers thought they were allowlisting."* [5](#0-4) 

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd users, whitelisted market makers) is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. The LP assets of the curated pool are exposed to unauthorized swaps, enabling adverse selection and direct loss of LP principal. This matches the "allowlist bypass" and "wrong-actor binding" impact categories.

---

### Likelihood Explanation

High. `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` targeting the curated pool. No special privilege or setup is required beyond knowing the pool address. The pool admin is likely to allowlist the router because without it, allowlisted users lose access to all router convenience features (multi-hop, exact-output, slippage protection).

---

### Recommendation

The extension must resolve the end-user identity from the router's transient callback context rather than trusting the `sender` parameter. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` (the end user) into `extensionData` before calling the pool. The extension decodes and checks that address. This requires a trusted encoding convention.

2. **Check `sender` and fall back to a router-reported origin**: The extension checks `allowedSwapper[pool][sender]` first; if `sender` is the router, it additionally checks a user-identity field the router must supply in `extensionData`.

3. **Gate by `recipient` instead of `sender`** (only if the pool's curation intent is to restrict who *receives* output, not who *initiates* the swap).

The simplest safe default is to document that the router must never be allowlisted and that allowlisted users must call the pool directly — but this should be enforced in code, not documentation.

---

### Proof of Concept

```solidity
// Setup: pool admin creates curated pool with SwapAllowlistExtension
// Admin allowlists alice (legitimate user) and the router (to let alice use it)
swapAllowlist.setAllowedToSwap(pool, alice, true);
swapAllowlist.setAllowedToSwap(pool, address(router), true);

// Attack: bob (not allowlisted) bypasses the allowlist via the router
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        tokenIn: token0,
        recipient: bob,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// ✓ Swap succeeds: extension saw sender=router, router is allowlisted
// ✓ Bob swapped on a pool he was never authorized to access
```

The extension receives `sender = address(router)`, which is allowlisted, so `allowedSwapper[pool][router]` returns `true` and the guard passes — even though `bob` is not allowlisted. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-42)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
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
