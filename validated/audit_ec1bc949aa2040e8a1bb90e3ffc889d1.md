Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, allowing any caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps using the `sender` argument, which the pool sets to `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is the immediate caller, so the allowlist check evaluates the router's address rather than the end user's address. Any pool that allowlists the router to support router-mediated swaps for legitimate users simultaneously grants unrestricted swap access to every address, including those individually blocked.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the hook.**

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as `sender` to every configured extension: [2](#0-1) 

**Step 2 — The allowlist checks `sender`, which is the router when routing is used.**

`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

**Step 3 — The router calls `pool.swap()` directly, making itself the `sender`.**

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` with the router as `msg.sender`: [4](#0-3) 

The same applies to `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

So `sender` = router address. The check becomes `allowedSwapper[pool][router]` — it evaluates the router, not the end user.

**Step 4 — Contrast with `DepositAllowlistExtension`.**

The deposit allowlist correctly checks `owner` (the economic actor), not `sender` (the immediate caller): [6](#0-5) 

The pool passes `owner` as a distinct argument from `msg.sender`, so the deposit guard is immune to the same router-mediation problem. The swap allowlist has no equivalent separation.

## Impact Explanation

`SwapAllowlistExtension` is the only on-chain mechanism for a pool admin to restrict which counterparties may trade against the pool's LP liquidity. When the guard is bypassed, unauthorized users execute swaps against the pool, exposing LP principal to counterparties the pool admin explicitly intended to block. Every swap by a non-allowlisted user that should have reverted instead settles normally, draining LP value at the oracle-anchored bid/ask spread to an unauthorized party. This constitutes a direct loss of user principal and broken core pool functionality (admin-boundary break: pool admin's allowlist restriction is bypassed by an unprivileged path).

## Likelihood Explanation

The bypass requires the pool admin to have allowlisted the router address. This is not a misconfiguration — it is the only way to allow allowlisted users to use the router at all, since the router is the `sender` the pool sees. Any pool that (a) uses `SwapAllowlistExtension` and (b) wants router support is forced into this state. The trigger is a normal, unprivileged call to `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) by any address. No special permissions, tokens, or setup beyond a standard router call are required.

## Recommendation

The `beforeSwap` hook should gate the economic actor, not the immediate caller. Two concrete approaches:

1. **Mirror the deposit allowlist pattern**: Introduce a separate `swapper` argument (analogous to `owner` in `addLiquidity`) that the pool populates from a caller-supplied field rather than from `msg.sender`, so the router can pass the real user address.
2. **Trusted-forwarder pattern**: The router encodes the original `msg.sender` (the end user) into `extensionData`; the extension decodes and checks it after verifying the router's identity.

The simplest safe interim fix is to document that `SwapAllowlistExtension` cannot be combined with router-mediated swaps and enforce this at the factory level (e.g., reject pool configurations that register both `SwapAllowlistExtension` and a public router as an allowed swapper).

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension as beforeSwap hook
  admin calls setAllowedToSwap(pool, alice, true)      // alice is allowlisted
  admin calls setAllowedToSwap(pool, router, true)     // router allowlisted to support alice's router swaps
  bob is NOT individually allowlisted

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...) with msg.sender = router
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] → TRUE
    → swap executes for bob, who was never allowlisted

Result:
  bob swaps against the pool's LP liquidity despite being individually blocked.
  alice's individual allowlist entry is irrelevant — the router entry grants access to everyone.
```

Foundry test outline:
1. Deploy pool with `SwapAllowlistExtension` as the `beforeSwap` hook.
2. Admin calls `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)`.
3. Assert `bob` (not allowlisted) calling `pool.swap()` directly reverts with `NotAllowedToSwap`.
4. Assert `bob` calling `router.exactInputSingle({pool: pool, ...})` succeeds — demonstrating the bypass.

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
