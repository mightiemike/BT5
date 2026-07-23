Audit Report

## Title
SwapAllowlistExtension Bypass via Router: Unprivileged Users Can Swap on Allowlisted Pools Through MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` resolves to the router address, not the end user. If the pool admin allowlists the router (the only way to let allowlisted users use the router), any unprivileged address can bypass the allowlist entirely by calling the router directly.

## Finding Description

**Root cause — identity mismatch in the allowlist check:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist: [1](#0-0) 

`msg.sender` here is the pool (correct). `sender` is whatever the pool passed as the first argument to the hook.

**The pool always passes `msg.sender` of its own `swap()` call as `sender`:** [2](#0-1) 

**`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:** [3](#0-2) 

**The router is the immediate caller of `pool.swap()`:**

`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` directly, making the pool's `msg.sender` the router contract, not the end user: [4](#0-3) 

The same applies to `exactInput` (multi-hop) and `exactOutputSingle`: [5](#0-4) [6](#0-5) 

**The dilemma the pool admin faces:**

| Admin choice | Effect |
|---|---|
| Router NOT allowlisted | Allowlisted users cannot use the router — broken UX |
| Router IS allowlisted | `allowedSwapper[pool][router] == true`, so every user who calls the router passes the check — full bypass |

There is no configuration that allows specific users to use the router while blocking others. The extension has no mechanism to inspect the actual end-user identity when an intermediary is involved.

## Impact Explanation

This is an admin-boundary break. The `SwapAllowlistExtension` is the production guard for restricting swap access on a pool. Once the router is allowlisted (the only practical choice for a pool that wants allowlisted users to use the standard periphery), the guard is completely inert for router-mediated swaps. Any unprivileged address can call `exactInputSingle`, `exactInput`, or `exactOutputSingle` on the router and trade against the pool's liquidity without appearing on the allowlist. The pool admin's intended access control is bypassed by an unprivileged path through a public periphery contract.

## Likelihood Explanation

High. The router is the standard, publicly documented entry point for swaps. Pool admins who deploy a `SwapAllowlistExtension` pool and want their allowlisted users to have a normal UX will allowlist the router. The bypass is then reachable by any address with no special privileges, no flash loan, and no malicious setup — a single call to `exactInputSingle` suffices.

## Recommendation

The extension must check the actual end-user identity, not the immediate caller. Two viable approaches:

1. **Pass the real user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it, then cross-checks that `msg.sender` (the pool) confirms the router is a trusted forwarder.
2. **Check `sender` for direct calls, decode user for router calls**: The extension inspects whether `sender` is a known trusted forwarder and, if so, reads the real user from a standardised field in `extensionData`.

Either way, the allowlist lookup must resolve to the economic actor (the address paying tokens and receiving output), not the contract that happened to call `pool.swap()`.

## Proof of Concept

```
Setup
─────
1. Pool admin deploys pool with SwapAllowlistExtension (extension1 = allowlist, beforeSwap order = 1).
2. Admin allowlists alice:  extension.setAllowedToSwap(pool, alice, true)
3. Admin allowlists router: extension.setAllowedToSwap(pool, router, true)
   (required so alice can use the router)

Attack
──────
4. eve (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: eve, ...})

5. Router calls pool.swap(eve, zeroForOne, amount, limit, "", "")
   → pool.msg.sender = router

6. Pool calls _beforeSwap(sender=router, ...)
   → extension.beforeSwap(sender=router, ...)
   → checks allowedSwapper[pool][router] == true  ✓ passes

7. Swap executes. Eve receives output tokens.
   SwapAllowlistExtension never saw eve's address.

Result: eve, who is not on the allowlist, successfully swaps on a pool
        that is supposed to be restricted to allowlisted users only.
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
