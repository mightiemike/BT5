Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address as Swapper Identity, Enabling Allowlist Bypass or Breaking Router Swap Path — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the immediate caller of `MetricOmmPool.swap`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the router is allowlisted rather than the actual user. This permanently misbinds the allowlist guard to the wrong actor on every router-mediated swap, producing either a full allowlist bypass (if the router is allowlisted) or a broken swap path for all legitimate users (if it is not).

## Finding Description
The call chain is confirmed by the production code:

1. `MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` directly with no user identity forwarded — the original `msg.sender` is only stored in transient storage for the payment callback, not passed to the pool as a swap actor identity. [1](#0-0) 

2. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, where `msg.sender` at that point is the router. [2](#0-1) 

3. `SwapAllowlistExtension.beforeSwap` receives that router address as `sender` and evaluates `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][router]` — never touching `allowedSwapper[pool][actual_user]`. [3](#0-2) 

The same flaw applies to `exactOutputSingle` (line 136) and all hops of `exactInput` (line 104) and `exactOutput` (line 165), since every pool call originates from the router contract. [4](#0-3) 

No existing guard compensates: `allowAllSwappers` is a separate bypass flag, and the `isAllowedToSwap` view function mirrors the same flawed lookup. [5](#0-4) 

## Impact Explanation
Two mutually exclusive fund-impacting failure modes:

**Mode A — Allowlist bypass (High):** A pool admin who allowlists the router address (the natural step to enable router-based swaps) inadvertently grants every unprivileged address the ability to swap on a pool intended to be restricted. The allowlist provides zero protection; `allowedSwapper[pool][router] == true` satisfies the guard for any caller.

**Mode B — Broken core swap path (Medium):** If the router is not allowlisted, every legitimately allowlisted user who attempts to swap through the router is rejected with `NotAllowedToSwap`. The primary public swap entrypoint is permanently broken for all users of allowlisted pools.

Both modes violate the protocol invariant that a curated pool enforces its allowlist policy regardless of which supported public entrypoint is used, constituting broken core pool functionality and/or loss of access to funds.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary documented public swap entrypoint. Pool admins configuring `SwapAllowlistExtension` who want their users to use the router must allowlist the router, immediately triggering Mode A. Any user who discovers the router is allowlisted can exploit it with a single `exactInputSingle` call requiring no special privileges or setup. The `exactInput` multi-hop path has the same flaw for every hop.

## Recommendation
The extension must receive the original user's address, not the immediate pool caller. The simplest correct fix is for the router to pass the original `msg.sender` through `extensionData` (alongside the existing transient-storage callback context) and for `SwapAllowlistExtension.beforeSwap` to decode and check it when `sender` is a known router. A more robust protocol-level fix is to define a standard attestation field in `extensionData` that routers are required to populate with the originating user, and for the extension to prefer that field over `sender` when present.

## Proof of Concept
```solidity
// Setup: pool configured with SwapAllowlistExtension
// Pool admin allowlists the router so legitimate users can swap via router
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not individually allowlisted) calls the router directly.
// Extension sees sender = address(router), which IS allowlisted → passes.
// allowedSwapper[pool][attacker] == false, but allowedSwapper[pool][router] == true
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:             address(pool),
    recipient:        attacker,
    zeroForOne:       true,
    amountIn:         1_000e18,
    amountOutMinimum: 0,
    priceLimitX64:    0,
    deadline:         block.timestamp,
    tokenIn:          token0,
    extensionData:    ""
}));
// Swap executes successfully despite attacker not being individually allowlisted.
```

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L27-29)
```text
  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
