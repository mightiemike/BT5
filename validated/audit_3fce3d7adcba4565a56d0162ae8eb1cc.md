Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on `sender`, which is `msg.sender` of `pool.swap`. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the user. A pool admin who allowlists the router to enable router-based swaps inadvertently opens the gate to every user, defeating the per-user restriction entirely.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` of `pool.swap` and therefore the `sender` arriving at the extension: [3](#0-2) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` ignores `sender` and checks `owner` (the LP position owner / economic actor), explicitly supporting the operator pattern: [4](#0-3) 

The swap extension never received the same treatment, leaving the check bound to the intermediary (router) rather than the economic actor (user).

## Impact Explanation

A pool admin who deploys a restricted pool (e.g., KYC-gated or market-maker-only) and configures `SwapAllowlistExtension` faces two broken outcomes:

1. **Allowlist bypass (higher impact):** The admin allowlists the router so that allowlisted users can swap through it. Because the check is on the router address, every user — including those not on the allowlist — can now swap freely through the router. The per-user restriction is completely nullified. This satisfies the "admin-boundary break" impact gate: the pool's access-control invariant is broken by an unprivileged path (routing through the router).

2. **Allowlisted users locked out (lower impact):** The admin does not allowlist the router. Allowlisted users who attempt to swap through `MetricOmmSimpleRouter` are rejected because the router is not in the allowlist, even though the user is. Core swap functionality is broken for the intended user set.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool admin who wants users to swap through the router must allowlist it, and doing so is a natural, expected configuration step. The bypass activates under normal, non-adversarial usage. No special privileges or malicious setup are required beyond the admin making a reasonable operational decision. The attacker (any non-allowlisted user) simply calls `MetricOmmSimpleRouter` with no special preconditions.

## Recommendation

Mirror the deposit extension's approach: check the actual user identity rather than the direct caller. Two options:

**Option A — Add a `swapper` field to `extensionData`:** Require callers (including the router) to encode the actual user address in `extensionData`; the extension decodes and checks it. The router already has access to `msg.sender` (the user) and can encode it.

**Option B — Check `recipient` instead of `sender`:** If the protocol convention is that the recipient of swap output is always the economic actor, check `recipient` (the second parameter, currently ignored by `beforeSwap`). This is consistent with how `owner` is used in the deposit path.

Either fix must be applied consistently so that the checked address is the economic actor, not the intermediary.

## Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
3. Pool admin calls setAllowedToSwap(pool, router, true)  // allow router so alice can use it
4. Bob (not KYC'd) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
   → router calls pool.swap(recipient, ...)
   → pool calls _beforeSwap(sender=router, ...)
   → extension checks allowedSwapper[pool][router] == true  ✓
   → Bob's swap succeeds despite not being on the allowlist
5. Carol (not KYC'd) calls pool.swap(...) directly
   → pool calls _beforeSwap(sender=carol, ...)
   → extension checks allowedSwapper[pool][carol] == false  ✗
   → Carol's direct swap reverts — but her router swap would succeed
```

The allowlist is enforced only for direct pool callers; the router is a universal bypass key once allowlisted. [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
