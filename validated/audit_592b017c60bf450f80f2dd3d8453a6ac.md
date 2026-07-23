Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` of the pool call. When `MetricOmmSimpleRouter` mediates a swap, `msg.sender` of `pool.swap()` is the router contract, not the end user. A pool admin who allowlists the router — required for any router-mediated swap — inadvertently opens the pool to every user who calls the router, defeating the per-user allowlist entirely.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted on the calling pool via `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the original `msg.sender` to the pool. The original caller is stored only in transient storage for the payment callback and is never surfaced to the pool or any extension: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. Consequently, the extension always sees `sender = router address` for every router-mediated swap, regardless of who initiated the transaction. This creates an irresolvable dilemma: not allowlisting the router breaks all periphery usage for legitimate users; allowlisting the router lets every caller bypass the per-user restriction.

## Impact Explanation
A curated pool (e.g., KYC-only, institutional-only) deploying `SwapAllowlistExtension` loses its access control the moment the pool admin allowlists the router to support standard periphery usage. Any unprivileged user can call `exactInputSingle` or `exactInput` on the router targeting the pool and execute swaps the allowlist was designed to block. This constitutes a broken core pool functionality (access-controlled swap enforcement is completely bypassed) and an admin-boundary break (the pool admin's per-user restriction is circumvented by an unprivileged path). LP providers suffer unauthorized swap exposure, fee leakage, and potential arbitrage losses from actors the pool was explicitly designed to exclude.

## Likelihood Explanation
The scenario is directly reachable by any public user with no special privileges. The only precondition is that the pool admin has allowlisted the router — a step any operator would take to make the pool usable through the standard periphery. The router is a production contract deployed alongside the pool system, making this a normal operational configuration. The attack is repeatable with no cost beyond gas.

## Recommendation
The `sender` argument passed to extension hooks must represent the economic actor, not the immediate contract caller. Two complementary fixes:

1. **Router-level**: Have the router encode the original `msg.sender` into `extensionData` (or a dedicated parameter) so extensions can extract and verify the true initiator.
2. **Extension-level**: `SwapAllowlistExtension.beforeSwap` should decode a verified-caller field from `extensionData` when `sender` is a known router, or the pool should expose a mechanism (e.g., transient storage readable by extensions) that carries the original user identity through the call stack.

Until fixed, pool admins must be warned that `SwapAllowlistExtension` provides no meaningful per-user restriction for router-mediated swaps.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
  pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted for periphery use
  bob is NOT allowlisted

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...) with msg.sender = router
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true
    → hook passes, swap executes
    → bob successfully swaps in a pool he is not authorized to access

Root cause: allowedSwapper[msg.sender][sender] resolves to
allowedSwapper[pool][router], not allowedSwapper[pool][bob].
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
