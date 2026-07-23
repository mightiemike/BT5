Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of end-user, allowing any caller to bypass per-user swap gating via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual end-user. A pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously opens the pool to every address on-chain, completely defeating the per-user allowlist invariant.

## Finding Description
In `MetricOmmPool.swap`, the pool passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` (via `ExtensionCalling`) encodes that `sender` value and forwards it to every configured extension, including `SwapAllowlistExtension`.

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)`, the router is `msg.sender` of that call: [3](#0-2) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The same structural problem exists for `exactInput`, `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

The pool admin faces an irresolvable dilemma: not allowlisting the router breaks router access for all legitimate users; allowlisting the router opens the pool to every address on-chain. There is no existing guard in `SwapAllowlistExtension` that decodes or verifies the original caller behind the router.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, regulated LPs) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. This constitutes broken core pool functionality: the allowlist guard — a production extension — fails open on the standard periphery path. Unauthorized users can execute swaps against the pool, bypassing access controls the pool admin explicitly configured to protect LP positions and regulatory compliance requirements.

## Likelihood Explanation
High. `MetricOmmSimpleRouter` is the production periphery contract end-users are expected to use. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — any EOA can call `exactInputSingle` on the router pointing at the restricted pool.

## Recommendation
**Short term:** Document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` when per-user gating is required. Pool admins must choose between direct-call-only access or open router access.

**Long term:** Redesign the identity passed to `beforeSwap`. Have the router encode the actual `msg.sender` (the end-user) in `extensionData`, and have `SwapAllowlistExtension` decode and check that value when present. Alternatively, introduce a trusted-forwarder pattern where the router attests the real caller and the extension verifies the attestation. The checked identity must be the economically relevant actor, not the intermediary contract.

## Proof of Concept
```solidity
// Setup:
// 1. Pool deployed with SwapAllowlistExtension as before-swap hook.
// 2. Pool admin allowlists alice AND the router:
//    swapExtension.setAllowedToSwap(pool, alice, true);
//    swapExtension.setAllowedToSwap(pool, address(router), true);
//
// Attack:
// 3. Bob (not allowlisted) calls the router:
//    router.exactInputSingle(ExactInputSingleParams({
//        pool: pool,
//        recipient: bob,
//        zeroForOne: true,
//        amountIn: 1_000e18,
//        ...
//    }));
//
// Result:
// - pool.swap() called with msg.sender = router
// - _beforeSwap passes sender = router to the extension
// - extension checks allowedSwapper[pool][router] → true
// - Bob's swap executes despite Bob never being allowlisted
``` [5](#0-4)

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
