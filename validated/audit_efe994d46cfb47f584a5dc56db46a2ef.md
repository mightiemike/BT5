Audit Report

## Title
`SwapAllowlistExtension` checks router address as `sender` instead of originating EOA, enabling full allowlist bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract address, not the originating EOA. If a pool admin allowlists the router to enable router-mediated swaps for curated users, every unpermissioned user can bypass the allowlist by routing through the same public router, executing real swaps against restricted pool liquidity.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` (the immediate caller) as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // always the immediate caller of the pool
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value verbatim to every configured extension via `abi.encodeCall`. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

At this point `msg.sender` inside the pool is the **router address**, so `sender` delivered to `SwapAllowlistExtension` is the router, not the originating EOA. The allowlist lookup becomes `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user_EOA]`.

This creates two mutually exclusive broken states:
1. **Allowlisted EOAs cannot use the router.** If the admin allowlists specific EOAs but not the router, every router-mediated swap by those EOAs reverts `NotAllowedToSwap`.
2. **Any user bypasses the allowlist via the router.** If the admin allowlists the router address to fix (1), every unpermissioned user can call `router.exactInputSingle` and the extension sees `sender = router` → allowed, regardless of whether the originating EOA is on the allowlist.

The multi-hop `exactInput` path compounds this: for hops after the first, the payer is `address(this)` (the router itself), so the router address appears as `sender` on every intermediate pool in the path.

No existing guard prevents this. The `_requireExpectedCallbackCaller` check in the router only validates the callback caller, not the swap initiator identity. There is no mechanism in the pool or extension interface to propagate the originating EOA.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd users, whitelisted market makers) can be fully bypassed by any unpermissioned user routing through the public `MetricOmmSimpleRouter`. The bypassing user executes real swaps against pool liquidity at oracle prices, consuming LP assets that the pool was configured to reject. This is a direct allowlist bypass with fund-impacting consequences: LP assets are consumed by trades the pool admin intended to restrict. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" impact criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint deployed alongside the protocol. Any pool that enables `SwapAllowlistExtension` and also needs to support router-mediated swaps (the normal user flow) must allowlist the router, at which point the bypass is trivially reachable by any EOA with no special privileges. The attacker only needs to call a public router function with no setup beyond having tokens to swap.

## Recommendation
The extension must receive the originating user identity, not the immediate pool caller. Two approaches:

1. **Pass the original initiator through `extensionData`.** The router encodes `msg.sender` into `extensionData` before calling the pool. `SwapAllowlistExtension.beforeSwap` decodes the originator from `extensionData` when `sender` is a known periphery contract, and checks that address instead.

2. **Add an `originator` field to the pool's `swap` signature.** The pool forwards this to extensions alongside `sender`. `SwapAllowlistExtension` checks `originator` instead of `sender`. This is the cleanest fix as the pool's `swap` signature already accepts `extensionData` which the router can use to encode the originating EOA.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (necessary so that allowlisted users can use the router)
  - Pool admin calls setAllowedToSwap(pool, alice, true)
  - Pool admin does NOT call setAllowedToSwap(pool, bob, true)

Attack:
  1. Bob (not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool: pool,
           recipient: bob,
           ...
       })

  2. Router calls pool.swap(bob, ...) with msg.sender = router

  3. Pool calls _beforeSwap(router, bob, ...)

  4. SwapAllowlistExtension.beforeSwap receives sender = router
     Checks: allowedSwapper[pool][router] == true → passes

  5. Swap executes. Bob receives output tokens.
     The allowlist check never saw Bob's address.

Result: Bob, who is not on the allowlist, successfully swaps on a
        curated pool that was supposed to restrict trading to alice only.
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
