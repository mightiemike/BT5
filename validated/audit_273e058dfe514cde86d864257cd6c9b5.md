Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any Caller to Bypass a Curated Pool's Swap Allowlist — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives the `sender` argument that `MetricOmmPool.swap()` populates with its own `msg.sender`. When swaps are routed through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract address, not the originating user. A pool admin who adds the router to the allowlist (the necessary step to let approved users trade via the standard periphery) simultaneously grants unrestricted swap access to every caller of the router, nullifying the allowlist entirely.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient,
    ...
    extensionData
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the `sender` parameter forwarded to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension is the pool):

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` inside the pool:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The real end-user address is stored in transient storage via `_setNextCallbackContext` solely for the payment callback — it is never forwarded to the pool or the extension. The `extensionData` passed to `pool.swap()` is `params.extensionData` supplied by the caller, not a router-injected user identity. [5](#0-4) 

The same structural issue applies to `exactInput` multi-hop paths, where intermediate hops use `address(this)` (the router) as the payer: [6](#0-5) 

The allowlist lookup therefore becomes `allowedSwapper[pool][router]`. Once the router is allowlisted, the check passes for every caller of the router — the actual end-user identity is never inspected.

## Impact Explanation

A curated pool protected by `SwapAllowlistExtension` is designed to restrict trading to a known set of counterparties (e.g., to prevent toxic flow, enforce KYC, or limit LP exposure). Once the pool admin adds the router to the allowlist — the necessary step to let their approved users trade via the standard periphery — the allowlist is effectively nullified. Any unprivileged user can call `exactInputSingle` on the router and swap against the pool's LP positions. This exposes LP funds to the full universe of traders the allowlist was meant to exclude, constituting a direct loss of LP principal through toxic or adversarial flow. This meets the contest threshold for a High severity finding: broken core pool functionality (allowlist access control) causing direct loss of LP assets.

## Likelihood Explanation

Likelihood is high. The router is the canonical, documented swap entrypoint for the protocol. A pool admin who deploys a curated pool and wants their allowlisted users to use the standard UI/router will naturally add the router to the allowlist. The misconfiguration is not obvious: the admin sees "router is allowed" and "user is allowed" as two separate entries, not realizing that allowlisting the router grants access to all router callers. No special attacker capability is required — any EOA can call `MetricOmmSimpleRouter.exactInputSingle`.

## Recommendation

The extension must check the economic actor — the address that initiated the trade and will bear its cost — not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Forward the originating user through `extensionData`.** The router should ABI-encode the real `msg.sender` into `extensionData` before calling `pool.swap()`. `SwapAllowlistExtension.beforeSwap` would then decode and check that value. To prevent spoofing, the extension should maintain a registry of trusted routers and only trust the forwarded address when `sender` (the pool's `msg.sender`) is a known router.

2. **Reject router-mediated calls on allowlisted pools.** The extension can detect router-mediated calls by comparing `sender` against a registry of known periphery contracts and reverting unless the actual user is separately verified.

## Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension configured.
  2. Pool admin calls setAllowedToSwap(pool, alice, true)  — alice is the only intended swapper.
  3. Pool admin calls setAllowedToSwap(pool, router, true) — to let alice use the router.

Attack:
  4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  5. Router calls pool.swap(recipient, zeroForOne, ...) — router is msg.sender inside pool.
  6. pool._beforeSwap(msg.sender=router, ...) → extension.beforeSwap(sender=router, ...).
  7. allowedSwapper[pool][router] == true → check passes.
  8. Bob's swap executes against LP positions; allowlist is bypassed.

Result: Bob, an unprivileged user, trades against a pool restricted to alice only.
``` [7](#0-6) [4](#0-3) [1](#0-0)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```
