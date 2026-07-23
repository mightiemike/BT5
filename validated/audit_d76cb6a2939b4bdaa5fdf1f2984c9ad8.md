Audit Report

## Title
`SwapAllowlistExtension` gates on router address instead of actual user, enabling allowlist bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the originating user. A pool admin who allowlists the router so that approved users can access the standard UI inadvertently grants every unprivileged user the ability to bypass the per-user swap allowlist.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the pool see `msg.sender = router`: [4](#0-3) 

The actual user identity is stored in transient storage via `_setNextCallbackContext` as the payer and is never surfaced to the extension. The allowlist check therefore becomes "Is the router allowed?" rather than "Is the user allowed?". By contrast, `DepositAllowlistExtension.beforeAddLiquidity` checks `owner` (the position recipient), which is passed explicitly and is router-independent: [5](#0-4) 

## Impact Explanation
A pool admin deploying a curated pool (e.g., KYC-only, institutional-only) with `SwapAllowlistExtension` and who allowlists the router so that approved users can use the standard UI inadvertently opens the pool to all users. Any non-allowlisted address can call `router.exactInputSingle(pool, ...)` and execute swaps at oracle-derived prices against LP capital that was never intended to be accessible to them. This constitutes a direct bypass of the pool's access-control invariant and results in LP funds being traded against by actors the pool admin explicitly excluded — a direct loss of LP principal protection and a broken core pool access-control guarantee.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical periphery swap entry point. Pool admins who want allowlisted users to use the standard router UI must allowlist the router address; otherwise their own approved users cannot swap through the router. This creates a forced choice: allowlist the router (opening the pool to everyone) or block the router (breaking the standard UX for approved users). The bypass is reachable through normal, expected usage of the supported periphery with no special attacker capability required.

## Recommendation
The `beforeSwap` hook must gate on the economically relevant actor. Two approaches:

1. **Pass the original initiator explicitly**: Encode the original `msg.sender` from the router into `extensionData` and have the extension decode and check that value. The router already tracks the payer in transient storage via `_getPayer()` and could encode it into `extensionData` before calling `pool.swap()`.

2. **Mirror the deposit pattern**: Gate on `recipient` (the economic beneficiary of the swap output) instead of `sender`, since `recipient` is passed explicitly and is router-independent — analogous to how `DepositAllowlistExtension` checks `owner`.

Until fixed, pool admins must be warned not to allowlist the router address, and the router must not be allowlisted by default in any deployment tooling or documentation.

## Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension configured.
2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
3. Admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use the UI
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
   → router calls pool.swap(bob, ...)           [msg.sender at pool = router]
   → pool calls _beforeSwap(router, bob, ...)
   → extension checks: allowedSwapper[pool][router] == true  ✓  (passes!)
5. Bob's swap executes against LP capital at oracle prices.
   Alice's LP position is traded against by an actor the admin explicitly excluded.
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
