Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the originating user, allowing any caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` evaluates the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. Any pool admin who allowlists the router to enable router-mediated swaps inadvertently grants every user on the network the ability to bypass the per-user allowlist.

## Finding Description
In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // direct caller of pool.swap(), NOT the end-user
    ...
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged as the `sender` argument in the ABI-encoded call to each extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates that argument:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is the router. The check becomes `allowedSwapper[pool][router]`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no forwarding of the original caller:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        ...
    );
``` [4](#0-3) 

The same pattern applies to `exactInput` (L104), `exactOutputSingle` (L136), and `exactOutput` (L165). The router stores the original `msg.sender` only in transient callback context for payment purposes (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)` at L71), but never passes it to the pool or extension. [5](#0-4) 

A pool admin who wants to allow router-mediated swaps must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once that entry exists, the extension passes for every user who calls through the router, regardless of whether that user is individually allowlisted. There is no existing guard that re-checks the originating user identity anywhere in the call chain.

## Impact Explanation
Any user can bypass the `SwapAllowlistExtension` gate on a restricted pool by routing through `MetricOmmSimpleRouter`. The allowlist's purpose — restricting swaps to a curated set of counterparties (e.g., KYC'd users, institutional traders, whitelisted protocols) — is completely nullified for router-mediated paths. Unauthorized users can execute swaps and extract value that the pool operator intended to reserve for approved counterparties only. This constitutes a broken core pool access-control mechanism causing direct loss of funds to pool LPs and the pool operator.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary production entry point for end-users. Any pool that deploys `SwapAllowlistExtension` and also expects users to use the router must allowlist the router — this is the standard operational configuration. No special privileges, flash loans, or unusual token behavior are required; a plain `exactInputSingle` call suffices. The bypass is reachable by any user in every such deployment.

## Recommendation
Pass the original end-user identity through the extension rather than the direct `pool.swap()` caller. Two options:

1. **Preferred — encode the originating user in `extensionData`.** The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that address. This requires a coordinated change in both the router and the extension.

2. **Alternative — check `recipient` instead of `sender`.** The recipient is the address that receives output tokens and is harder to spoof without economic loss, though it still allows a non-allowlisted user to route output to an allowlisted address.

Additionally, update the `SwapAllowlistExtension` NatSpec to document that `sender` is the direct caller of `pool.swap()`, not the originating user, so pool admins understand the implication of allowlisting router contracts.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the only approved user
  allowedSwapper[pool][router] = true         // admin adds router to enable router swaps

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ...})
      → pool.swap(msg.sender=router, ...)
        → _beforeSwap(sender=router, ...)
          → extension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  → PASSES
  bob successfully swaps despite not being individually allowlisted
```

Foundry test outline:
1. Deploy pool with `SwapAllowlistExtension` configured.
2. Call `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)`.
3. As `bob` (not allowlisted), call `router.exactInputSingle(...)` targeting the pool.
4. Assert the swap succeeds — confirming the bypass.
5. As `bob`, call `pool.swap(...)` directly and assert it reverts with `NotAllowedToSwap` — confirming the check works only for direct callers.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
