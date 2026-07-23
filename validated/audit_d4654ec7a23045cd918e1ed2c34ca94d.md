Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address as `sender` instead of originating EOA, enabling full allowlist bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is bound to `msg.sender` inside `MetricOmmPool.swap` — the direct caller of the pool. When swaps are routed through `MetricOmmSimpleRouter`, `sender` resolves to the router contract address, not the originating EOA. Any pool admin who allowlists the router to enable router-mediated swaps for their curated users simultaneously opens the pool to every user who calls the same public router, completely defeating the allowlist.

## Finding Description
**Root cause — wrong actor binding in `beforeSwap`:**

`SwapAllowlistExtension.beforeSwap` performs the check:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
Here `msg.sender` is the pool (the extension is called by the pool via `_callExtensionsInOrder`) and `sender` is the first parameter forwarded by the pool.

**Call path — how `sender` is bound:**

In `MetricOmmPool.swap`, the pool calls:
```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```
`ExtensionCalling._beforeSwap` then encodes this as the `sender` argument forwarded to every configured extension via `_callExtensionsInOrder`.

When a user (bob) calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)` directly:
```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```
So `msg.sender` inside `pool.swap` is the **router address**, not bob. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][bob]`.

**The inescapable trap for pool admins:**

A pool admin cannot simultaneously allow specific users to use the router while blocking others. If the router is allowlisted (to let alice use it), every user — including non-allowlisted bob — can bypass the allowlist by routing through the same public router contract. There is no configuration that achieves per-user router access control because the router is a shared public contract and the extension cannot distinguish between different EOAs behind it.

**Existing guards are insufficient:** The only guard is `allowedSwapper[pool][sender]`, and `sender` is structurally bound to the router address for all router-mediated swaps. No secondary check on the originating EOA exists anywhere in the call path.

## Impact Explanation
A non-allowlisted user can execute swaps on a curated pool by routing through `MetricOmmSimpleRouter`. The allowlist — the pool's primary access-control mechanism — is completely bypassed. LPs who deployed the pool for curated counterparties (e.g., KYC'd users, institutional partners) are exposed to unauthorized swaps from any EOA. This constitutes an **admin-boundary break**: the pool admin's allowlist policy is bypassed by an unprivileged path. Depending on pool configuration, this can result in direct loss of LP principal through unauthorized arbitrage or directional trading against the pool's pricing assumptions, and disruption of the pool's intended market dynamics.

## Likelihood Explanation
The bypass requires the pool admin to allowlist the router. This is a natural and expected administrative action: any pool admin who wants their allowlisted users to access deadline-protected swaps, exact-output swaps, or multi-hop routing via `MetricOmmSimpleRouter` must allowlist the router. The admin may not realize this simultaneously opens the pool to all users. The trigger is reachable through normal, well-intentioned pool administration. Once the router is allowlisted, the bypass is trivially repeatable by any EOA with no special privileges.

## Recommendation
The `SwapAllowlistExtension` must gate on the economically relevant actor — the originating EOA — not the intermediary contract. Two viable approaches:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` (e.g., as a leading 20-byte prefix), and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known/trusted router.

2. **Extension-side**: Add a registry of trusted forwarders to `SwapAllowlistExtension`. When `sender` is a registered forwarder, decode the real user from `extensionData` and apply the allowlist check against that address instead of the forwarder address.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  alice  → allowedSwapper[pool][alice]  = true   (alice is a curated user)
  router → allowedSwapper[pool][router] = true   ← admin adds this to let alice use the router

Attack (bob, not allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
    [MetricOmmPool.sol L230-240: _beforeSwap(msg.sender=router, ...)]
  ExtensionCalling._beforeSwap encodes sender=router, calls SwapAllowlistExtension.beforeSwap
    [ExtensionCalling.sol L149-177]
  SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] → true
    [SwapAllowlistExtension.sol L37]
  ✓ check passes — bob's swap executes on the curated pool

bob is never checked. The allowlist is fully bypassed.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
