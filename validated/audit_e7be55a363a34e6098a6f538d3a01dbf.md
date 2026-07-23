Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Complete Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is set to `msg.sender` at the pool level — the immediate caller of `pool.swap`. When `MetricOmmSimpleRouter` is used, the pool's `msg.sender` is the router, so the extension checks the router's address against the allowlist rather than the actual end-user. If the router is allowlisted (required for legitimate users to swap through it), any unprivileged user can bypass the curated-pool restriction with a single router call.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly without forwarding the originating user's address — the pool sees `msg.sender = router`: [4](#0-3) 

The same applies to `exactInput` (line 104), `exactOutputSingle` (line 136), and `exactOutput` (line 165). In every case, the pool's `msg.sender` is the router, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][end_user]`. [5](#0-4) 

No existing guard in the extension or pool recovers the originating user's address — `extensionData` is passed through but the extension ignores it entirely. [6](#0-5) 

## Impact Explanation

A pool admin deploying a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC-verified counterparties). In production, the router must be allowlisted so that legitimate users can swap through the standard periphery path. Once the router is allowlisted, every user on the network can bypass the restriction by calling any router entry-point — the extension sees `sender = router` and passes the check. The allowlist provides zero protection against router-mediated swaps. This is a broken core pool functionality / admin-boundary break with direct fund-impacting consequences: unauthorized parties can drain or trade against a pool intended to be restricted.

## Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap interface deployed by the protocol.
- The bypass requires no special privileges, no flash loans, and no multi-transaction setup: a single `exactInputSingle` call suffices.
- Pool admins have no mechanism within the current extension API to distinguish "router called on behalf of an allowlisted user" from "router called on behalf of an arbitrary user."
- The two failure modes are symmetric: if the router is allowlisted, the allowlist is fully bypassed; if it is not, allowlisted users cannot use the router at all.

## Recommendation

The `sender` forwarded to `beforeSwap` must represent the economic actor, not the intermediary. Two viable fixes:

1. **Extension-data forwarding:** Require the router to encode the original `msg.sender` in `extensionData`; the extension decodes and checks that address. This requires coordination between the router and the extension but preserves the current pool interface.
2. **Recipient-based gating:** Gate on `recipient` instead of `sender` when the pool is used with a router. This is semantically correct for curated pools where the economic beneficiary is the recipient.

The cleanest long-term fix is option 1 with a documented convention: the router always prepends the originating user address to `extensionData`, and `SwapAllowlistExtension` decodes it when present.

## Proof of Concept

```
Setup:
  - Deploy pool P with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(P, alice, true)
  - Pool admin calls setAllowedToSwap(P, router, true)  ← required for alice to use the router

Attack:
  1. bob (not allowlisted) calls:
       router.exactInputSingle({pool: P, recipient: bob, ...})
  2. Router calls pool.swap(bob, ...) — msg.sender at pool = router
  3. _beforeSwap(sender=router, ...) is dispatched
  4. SwapAllowlistExtension.beforeSwap:
       allowedSwapper[P][router] == true  →  check passes
  5. Swap executes; bob receives output tokens

Result:
  bob, who is not on the allowlist, successfully swaps on a curated pool.
  The SwapAllowlistExtension guard is completely bypassed.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
