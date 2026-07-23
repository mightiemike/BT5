Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual swapper, allowing any user to bypass the swap allowlist via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the pool's direct caller. When `MetricOmmSimpleRouter` calls `pool.swap`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router to enable legitimate router-mediated swaps, every non-allowlisted user can bypass the curated pool's access control by routing through the router with a single call.

## Finding Description
In `SwapAllowlistExtension.beforeSwap` (L37), the guard is:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
Here `msg.sender` is the pool and `sender` is the first argument forwarded by the pool.

In `MetricOmmPool.swap` (L230-231), the pool passes its own `msg.sender` as `sender`:
```solidity
_beforeSwap(msg.sender, recipient, ...);
```
`ExtensionCalling._beforeSwap` (L149-177) forwards this value verbatim to the extension with no transformation.

When `MetricOmmSimpleRouter.exactInputSingle` (L72-80) executes, it calls `IMetricOmmPoolActions(params.pool).swap(...)` directly. The pool therefore sees `address(router)` as `msg.sender` and passes it as `sender` to the extension. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

The same wrong-actor binding applies to `exactInput` (L103-112) for all hops, `exactOutputSingle` (L136-137), and `exactOutput` (L165-181). For intermediate hops in `exactInput`, the payer stored in callback context is `address(this)` (the router), so the extension again sees the router.

The dilemma for pool admins is inescapable:
- **Do not allowlist the router** → all router-mediated swaps revert, even for legitimately allowlisted users who prefer the router path.
- **Allowlist the router** (the only way to enable router-mediated swaps for legitimate users) → every non-allowlisted user bypasses the allowlist by calling any router swap function.

No existing guard recovers the original EOA. The `extensionData` field passed by the router is `""` (empty string) for all single-hop calls, so the extension has no mechanism to identify the real initiator.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd users, institutional partners, or protocol-controlled addresses is fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. This is an admin-boundary break: an unprivileged, publicly accessible periphery path circumvents a factory/pool role check. The allowlist guard silently fails open for all router-mediated swaps, breaking core pool access-control functionality. Any non-allowlisted user can execute swaps on a pool they are not authorized to use, which constitutes broken core pool functionality and an admin-boundary bypass meeting Sherlock contest thresholds.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entry point. The bypass requires no special privileges, no flash loans, and no multi-transaction setup — a single `exactInputSingle` call suffices. Any user who reads the periphery interface will naturally use the router. Likelihood is **High**.

## Recommendation
The extension must identify the economic actor (the EOA or contract that initiated the swap), not the immediate caller of `pool.swap`. Two approaches:

1. **Pass the original initiator through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted, enforced by checking a factory-registered router registry.

2. **Detect router callers and require real-user embedding:** The extension reads the pool's factory, checks whether `sender` is a registered router, and if so decodes the real user from `extensionData` and verifies the router's identity.

The simplest safe interim fix is to remove the router from the allowlist and require all curated-pool users to call `pool.swap` directly, but this breaks UX. The correct long-term fix is option 1 or 2 above.

## Proof of Concept
```solidity
// Setup:
// - Pool with SwapAllowlistExtension configured
// - allowedSwapper[pool][router] = true  (admin must set this for legitimate users to use router)
// - allowedSwapper[pool][allowedUser] = true
// - allowedSwapper[pool][attacker] = false

// Attacker bypasses the allowlist:
vm.prank(attacker); // attacker is NOT in allowedSwapper[pool]
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        tokenIn: token0,
        recipient: attacker,
        zeroForOne: true,
        amountIn: 1000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        extensionData: "",
        deadline: block.timestamp
    })
);
// Pool passes address(router) as sender to extension.
// Extension checks allowedSwapper[pool][router] == true → passes.
// Attacker swaps successfully on a pool they are not authorized to use.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
