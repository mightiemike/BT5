Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the real swapper, defeating per-pool swap allowlists - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, `sender` resolves to the router contract address, not the real user. A pool admin who allowlists the router to support periphery-mediated swaps inadvertently grants swap access to every address on the network; a pool admin who does not allowlist the router locks out all allowlisted EOAs from the supported periphery path.

## Finding Description
`SwapAllowlistExtension.beforeSwap` enforces the allowlist at line 37:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension caller) and `sender` is the first argument forwarded by the pool. [1](#0-0) 

`MetricOmmPool.swap()` passes `msg.sender` of its own call as the `sender` argument to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, so the pool sees `msg.sender = router`: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — in every router entry point the pool receives `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][realUser]`. No existing guard corrects this: the extension has no concept of trusted routers or embedded-identity payloads, and `extensionData` is passed through but never decoded by the extension.

## Impact Explanation
**Scenario A — Allowlist bypass (High):** A pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists the router so that approved users can trade via the standard periphery. Because the check resolves to `allowedSwapper[pool][router] = true`, every unprivileged address can call `router.exactInputSingle()` and pass the check. The allowlist is completely defeated — any user can trade on a pool intended to be restricted. This is an admin-boundary break reachable by any unprivileged caller with no special privilege required.

**Scenario B — Broken core swap functionality (Medium):** A pool admin allowlists specific EOAs but does not allowlist the router. Those EOAs cannot use `MetricOmmSimpleRouter` at all — every router-mediated swap reverts with `NotAllowedToSwap` even though the real user is on the allowlist. The supported periphery swap path is broken for every intended participant.

## Likelihood Explanation
The trigger is the standard, publicly documented periphery path. Any user who calls any router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) on a pool with `SwapAllowlistExtension` active reaches the vulnerable check. No flash loan, oracle manipulation, or special privilege is required. Likelihood is high whenever a pool is deployed with this extension and the router is the intended entry point.

## Recommendation
The extension must check the economic actor, not the immediate caller. Two complementary fixes:

1. **Pass the real user through the router.** The router already knows `msg.sender` (the real user) at entry. It should encode this address as the first word of `extensionData` for every `pool.swap()` call it makes.
2. **Decode the real user in the extension when `sender` is a trusted router.** The pool admin configures a set of trusted router addresses in `SwapAllowlistExtension`. When `sender` is a trusted router, the extension decodes the real user from `extensionData` and checks `allowedSwapper[pool][realUser]` instead.

The simplest safe fix: have the router prepend `abi.encode(msg.sender)` to `extensionData` before calling `pool.swap()`, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a registered trusted router.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. `attacker` (not on the allowlist) calls `router.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
5. Pool calls `_beforeSwap(router, ...)` → extension receives `sender = router`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `attacker` successfully swaps on a pool intended to be restricted to curated addresses.

The wrong value is the `sender` argument checked at `allowedSwapper[msg.sender][sender]` — it resolves to the router address instead of the real user's address, making the allowlist check meaningless for any router-mediated swap. [5](#0-4)

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
