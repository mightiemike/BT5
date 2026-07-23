Audit Report

## Title
SwapAllowlistExtension checks router address instead of actual swapper, allowing allowlist bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. Any pool admin who allowlists the router to enable router-mediated swaps for approved users simultaneously opens the gate to every unprivileged user who routes through the same router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← direct caller, not the economic actor
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against the allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` to the pool:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

For any pool that uses `SwapAllowlistExtension` and wants to support router-mediated swaps for its approved users, the admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for every caller regardless of whether that caller is individually permitted. The extension has no mechanism to simultaneously permit the router as an intermediary for allowlisted users while blocking non-allowlisted users from using that same router.

## Impact Explanation
This is a direct admin-boundary break. A pool configured with `SwapAllowlistExtension` is a restricted pool where the admin controls who may trade. Bypassing the allowlist allows unauthorized parties to execute swaps against LP positions that were never intended to be exposed to public flow, moving the pool cursor and causing LP losses at oracle-anchored prices. This constitutes a direct loss-of-LP-principal path and a broken core invariant ("only addresses I have approved may swap").

## Likelihood Explanation
The bypass requires the router to be allowlisted. This is the natural, expected configuration for any pool that wants to support multi-hop routing or the `exactInput`/`exactOutput` paths for its approved users. A pool admin who does not allowlist the router blocks all router-mediated swaps even for approved users. The misconfiguration is therefore the only practical way to operate the pool with router support, making the likelihood medium-high for any pool that uses both the allowlist extension and the router.

## Recommendation
The extension must check the identity of the economic actor, not the intermediary. Two viable fixes:

1. **Check `recipient` instead of `sender`**: Replace `sender` with `recipient` in the allowlist lookup in `SwapAllowlistExtension.beforeSwap`. The `recipient` is already available as the second parameter and represents the economically relevant party receiving swap output.

2. **Require the router to forward the user's address in `extensionData`**: The extension decodes the actual user from `extensionData` and checks that address. The router must be updated to populate this field, and the extension must reject calls where `extensionData` is empty or malformed.

Option 1 is simpler and does not require router changes, but gates on who receives output rather than who initiates the swap. Option 2 is more precise but requires coordinated changes across the router and extension.

## Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension in beforeSwap slot
  admin calls setAllowedToSwap(pool, router, true)   // to enable router-mediated swaps
  admin calls setAllowedToSwap(pool, alice, true)    // alice is the only intended user
  bob is NOT on the allowlist

Attack:
  bob calls router.exactInputSingle({
      pool:      pool,
      tokenIn:   token0,
      tokenOut:  token1,
      zeroForOne: true,
      amountIn:  X,
      recipient: bob,
      ...
  })

  router calls pool.swap(recipient=bob, ...)
    → pool passes sender=router to _beforeSwap  [MetricOmmPool.sol L231]
    → extension checks allowedSwapper[pool][router] == true  ✓  [SwapAllowlistExtension.sol L37]
    → swap executes; bob receives token1 output

Result:
  bob, who is not allowlisted, successfully swaps in a pool the admin
  believed was restricted to alice only.
```