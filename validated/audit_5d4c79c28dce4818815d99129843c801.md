Audit Report

## Title
SwapAllowlistExtension Gates on Router Address Instead of Original User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the originating user. Any pool admin who allowlists the router to permit legitimate router-mediated swaps simultaneously grants every unprivileged user the ability to bypass the per-user restriction.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router contract the `msg.sender` seen by the pool: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

The allowlist is keyed by `(pool, sender)` where `sender` is the router address. The pool admin faces an impossible choice: allowlist the router (every user can bypass the restriction) or do not allowlist the router (legitimate users cannot use the router at all). No configuration simultaneously allows legitimate users to use the router and blocks unauthorized users.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to authorized addresses (e.g., a KYC-gated pool, a private institutional pool, or a pool intended only for its own hedging bot) is fully open to any user who routes through `MetricOmmSimpleRouter`. The unauthorized user can execute swaps at oracle-derived prices the pool's LPs did not intend to offer to the general public, resulting in direct loss of LP principal and owed fees above Sherlock thresholds.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard public entry point for swaps. Any user who discovers a restricted pool needs only to call `exactInputSingle` on the router with the pool address. No privileged access, no special setup, and no malicious initial configuration is required — a single public transaction suffices. The condition is trivially reachable and repeatable.

## Recommendation
The extension must recover the original user's address rather than trusting the `sender` argument. Options include:
1. Require the router to encode the originating `msg.sender` into `extensionData` and have the extension verify it against a trusted-router registry.
2. Detect when `sender` is a known router and require the router to attest the real user in `extensionData`.
3. Document and enforce at the factory or router level that pools using `SwapAllowlistExtension` must not be reachable through `MetricOmmSimpleRouter` unless `allowAllSwappers` is `true`.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required so that legitimate users can swap through the router).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({
        pool: restrictedPool,
        recipient: attacker,
        zeroForOne: true,
        amountIn: largeAmount,
        ...
    })
  - Router calls pool.swap(...) with msg.sender = router.
  - Pool calls SwapAllowlistExtension.beforeSwap(sender=router, ...).
  - Extension checks allowedSwapper[pool][router] == true → passes.
  - Swap executes; attacker receives output tokens.

Result:
  - attacker, who was never individually allowlisted, successfully swaps
    against a pool configured to restrict access.
  - LP funds are exposed to an unauthorized counterparty.
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
