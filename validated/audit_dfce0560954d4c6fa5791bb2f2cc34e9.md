Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass Per-User Swap Gating via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool, which is always `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates the router's allowlist status rather than the actual user's. Any pool admin who allowlists the router to enable router-based swaps for their authorized users simultaneously grants unrestricted swap access to every EOA on the network.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension via `_callExtensionsInOrder`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender` value, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no mechanism to forward the original `msg.sender` into `extensionData`: [4](#0-3) 

The router stores the original caller only in transient storage for the payment callback (`_setNextCallbackContext`), but this is never surfaced to the pool or extensions. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. There is no configuration that simultaneously allows router-based swaps and enforces per-user restrictions: not allowlisting the router breaks all router-based swaps for authorized users; allowlisting the router opens the pool to every EOA.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a specific set of addresses (e.g., KYC-verified counterparties or whitelisted market makers). If the admin allowlists the router to enable router-based swaps, any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against the pool's LP positions. LPs deposited under the assumption that only authorized counterparties would trade against them; unauthorized swaps expose LP principal to adversarial order flow and arbitrage extraction that the allowlist was specifically designed to prevent. This constitutes a direct loss of LP principal above Sherlock thresholds, satisfying the "broken core pool functionality causing loss of funds" and "admin-boundary break" impact categories.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who wants to support router-based swaps for their allowlisted users must allowlist the router, triggering the bypass. The router is a public, permissionless contract with no user-identity forwarding mechanism. The bypass requires only a standard router call from any EOA — no special privileges, no flash loans, no callbacks. The precondition (admin allowlisting the router) is the expected operational setup, not a misconfiguration.

## Recommendation
The `SwapAllowlistExtension` must gate on the original user, not the immediate pool caller. Two complementary fixes: (1) The router should encode `msg.sender` into `extensionData` and the extension should decode and verify it when `sender` is a known router address (trusted-forwarder pattern). (2) Alternatively, document and enforce at the factory level that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` without an explicit override, reverting pool creation if both are configured together.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Admin calls `swapExtension.setAllowedToSwap(pool, address(router), true)` to enable router-based swaps.
3. `userA` (not individually allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` is the router.
5. Pool calls `_beforeSwap(router, ...)` → `ExtensionCalling` encodes `sender = router` → extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. `userA` successfully swaps against LP funds despite never being individually authorized.

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
