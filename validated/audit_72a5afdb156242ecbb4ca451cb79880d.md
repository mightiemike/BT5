Audit Report

## Title
`SwapAllowlistExtension` gates on the immediate pool caller (router address) instead of the originating user, enabling full allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the value `MetricOmmPool.swap` passes as its own `msg.sender` — the immediate caller of `pool.swap()`. When `MetricOmmSimpleRouter` is that caller, the extension evaluates the router's allowlist entry, not the originating EOA's. Any pool admin who allowlists the router to support router-mediated swaps for legitimate users simultaneously opens the pool to every address on-chain.

## Finding Description

**Root cause — pool binds `sender` to its own `msg.sender`:**

`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // always the immediate caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

**Extension check — evaluates that bound value:**

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

where `msg.sender` is the pool and `sender` is the value forwarded from the pool — i.e., the immediate caller of `pool.swap()`. [2](#0-1) 

**Router call path — router is `msg.sender` at the pool boundary:**

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

The originating user's address is stored only in transient callback context (for payment purposes), not forwarded to the pool as `sender`. The pool sees the router as `msg.sender`, so `sender` passed to the extension is the router address. [3](#0-2) 

The same substitution occurs in `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (outer hop). [4](#0-3) 

For inner hops of `exactOutput`, `sender` becomes the **previous pool's address** (passed as `msg.sender` to the next `pool.swap()` call inside `_exactOutputIterateCallback`), an even more unexpected identity. [5](#0-4) 

**Existing guards are insufficient:** The `allowedSwapper` mapping is keyed by `(pool, swapper)` and is set per-address by the pool admin. There is no mechanism in the extension to distinguish a router-mediated call from a direct call, and no forwarding of the originating user identity through the call stack. [6](#0-5) 

## Impact Explanation

**Allowlist bypass (critical path):** A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses is fully bypassed by any EOA routing through `MetricOmmSimpleRouter` once the router is allowlisted. The attacker receives pool output tokens; LP providers bear the full economic exposure of an unrestricted swap — direct loss of the protection the allowlist was deployed to enforce. This satisfies the "admin-boundary break where an unprivileged path bypasses a factory/pool role check" impact gate.

**Broken core functionality (secondary path):** Allowlisted users who attempt to trade through the router are incorrectly rejected with `NotAllowedToSwap`, making the router entirely unusable on any allowlisted pool unless the admin sacrifices the allowlist's integrity. This satisfies the "broken core pool functionality causing unusable swap flows" impact gate.

## Likelihood Explanation

The router is the primary user-facing entry point for swaps, providing slippage protection, multi-hop routing, and deadline enforcement. Any pool admin deploying `SwapAllowlistExtension` who also wants allowlisted users to benefit from these features will naturally allowlist the router. There is no documentation warning against this. The bypass requires no special privilege — any EOA with a standard ERC-20 approval can exploit it by calling `exactInputSingle` or any other router entry point.

## Recommendation

The extension must gate on the originating user, not the immediate pool caller. Two sound approaches:

1. **Trusted-forwarder pattern in the router:** Before calling `pool.swap`, the router writes the originating `msg.sender` into a transient storage slot. The extension reads that slot (via a known interface on the router) when `sender` equals the router address, and checks the originating user instead.

2. **Extension-data forwarding:** The router encodes the originating `msg.sender` into `extensionData`. The extension decodes it only when `sender` is a known, factory-registered router, preventing spoofing by arbitrary callers. The factory would need a router registry.

Either approach must ensure the extension never treats the router address as the economically relevant actor for allowlist purposes.

## Proof of Concept

```
Setup:
  pool  = MetricOmmPool with SwapAllowlistExtension
  admin allowlists: Alice (EOA), router (MetricOmmSimpleRouter)
  Bob   = non-allowlisted EOA

Attack:
  Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)
        msg.sender in pool = router
        pool calls _beforeSwap(router, ...)
            SwapAllowlistExtension.beforeSwap(sender=router, ...)
                allowedSwapper[pool][router] == true  ← passes
    ← swap executes; Bob receives output tokens

Result:
  Bob, who is not in the allowlist, successfully swaps on a curated pool.
  Alice's allowlist entry is irrelevant — the router entry is the effective gate.
```

The existing unit tests call the extension directly with `vm.prank(address(pool))` and pass the user address as `sender` — they never exercise the router path and therefore do not catch this binding error.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
