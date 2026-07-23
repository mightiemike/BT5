Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of actual user, enabling allowlist bypass or broken router access — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` populates as `msg.sender` — the immediate caller of the pool. When users route through `MetricOmmSimpleRouter`, the pool receives `sender = router address`, not the actual user. This creates two mutually exclusive failure modes: either the router is allowlisted and any user bypasses the per-user gate entirely (High), or the router is not allowlisted and allowlisted users cannot use the supported periphery path at all (Medium).

## Finding Description

`SwapAllowlistExtension.beforeSwap` enforces its gate against the `sender` argument: [1](#0-0) 

`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly, making `msg.sender` to the pool the router contract: [3](#0-2) 

The actual user's address is stored only in transient callback context via `_setNextCallbackContext` and is never forwarded to the extension: [4](#0-3) 

The same wrong-actor binding applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (all recursive hops via `_exactOutputIterateCallback`): [5](#0-4) 

`pool.swap()` has no `swapper` parameter separate from `msg.sender` — the signature only accepts `recipient`, with no way to distinguish the economic actor from the caller: [6](#0-5) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates by `owner` — a distinct, user-controlled argument passed explicitly through `addLiquidity` — not by `sender`: [7](#0-6) 

`addLiquidity` passes `owner` as a separate parameter, so the deposit allowlist correctly identifies the economic actor regardless of which periphery contract calls the pool. No equivalent mechanism exists for swaps. [8](#0-7) 

## Impact Explanation

**Mode A — Allowlist bypass (High):** Pool admin allowlists specific users and also allowlists the router (the natural step to let those users trade via the supported periphery). Any non-allowlisted user calls `router.exactInputSingle`; the extension sees `sender = router`, which is allowlisted, and the check passes. The curated pool's access control is completely defeated — unauthorized users trade on a pool whose oracle-anchored pricing or LP composition was designed for a restricted set of counterparties, directly harming LP principal.

**Mode B — Broken core functionality (Medium):** Pool admin allowlists specific users but does NOT allowlist the router. Those users cannot use the router at all — every router-mediated swap reverts with `NotAllowedToSwap` because `sender = router` is not in the allowlist. The supported public swap path is unusable for the intended participants.

Both modes meet Sherlock thresholds: Mode A is a direct loss-of-funds impact on LP principal via unauthorized access; Mode B is broken core swap functionality for the intended user set.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entry point. Mode A requires only that the pool admin takes the natural configuration step of allowlisting the router so that allowlisted users can trade — a step any reasonable admin would take. The attacker (charlie) needs only to call `exactInputSingle`, a standard permissionless action with no special privileges, flash loans, or multi-step setup. The pool admin has no on-chain mechanism to distinguish router-mediated swaps by allowlisted users from those by non-allowlisted users, so there is no correct allowlist configuration that achieves the intended policy.

## Recommendation

Follow the `DepositAllowlistExtension` pattern: the pool must forward the actual economic actor as a distinct argument. The cleanest fix is extension-data forwarding: require the router to encode the actual `msg.sender` into `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known trusted router. This requires adding a `trustedRouter` registry in `SwapAllowlistExtension` so the extension knows when to decode the real user from `extensionData` versus checking `sender` directly. Alternatively, the `pool.swap()` interface could be extended with an explicit `swapper` parameter analogous to the `owner` parameter in `addLiquidity`, though this is a larger protocol-level change.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin: allowedSwapper[pool][alice] = true
              allowedSwapper[pool][router] = true   ← required for alice to use the router

Attack (Mode A):
  charlie (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: charlie, ...})

  router calls (MetricOmmSimpleRouter.sol L72-80):
    pool.swap(charlie, zeroForOne, amount, priceLimit, "", extensionData)
    // msg.sender to pool = router address

  pool calls (MetricOmmPool.sol L230-231):
    _beforeSwap(msg.sender=router, ...)

  extension checks (SwapAllowlistExtension.sol L37):
    allowedSwapper[pool][router] == true  ← PASSES

  Result: charlie swaps successfully on a curated pool he was never authorized to access.

Mode B (router not allowlisted):
  alice calls router.exactInputSingle(...)
  extension.beforeSwap(sender=router, ...)
  allowedSwapper[pool][router] == false → NotAllowedToSwap
  Result: alice cannot use the supported periphery path.
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L182-191)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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
