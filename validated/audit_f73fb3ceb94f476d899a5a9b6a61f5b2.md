Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address as `sender` instead of actual end-user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender` — the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. If the router is allowlisted (the only way to enable router-based swaps on a curated pool), every user unconditionally passes the check, defeating the per-user curation policy entirely.

## Finding Description

**Root cause:** `MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← always the direct caller of pool.swap()
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that value against its allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle`, `exactOutputSingle`, and `exactInput` all call `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

So the pool's `msg.sender` is always the router. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Two broken scenarios:**

1. **Allowlist bypass (high impact):** Pool admin allowlists the router so router-based swaps work. Because `allowedSwapper[pool][router] = true`, every user — including non-allowlisted ones — passes the check by routing through `MetricOmmSimpleRouter`. Per-user curation is completely defeated.

2. **Allowlisted users locked out of router (medium impact):** Pool admin does not allowlist the router. Allowlisted users who call the router get `NotAllowedToSwap` because `allowedSwapper[pool][router] = false`, even though their own address is allowlisted.

The `extensionData` field is forwarded by the router (`params.extensionData`) but `SwapAllowlistExtension.beforeSwap` declares the `bytes calldata` parameter unnamed and never reads it — there is no existing mechanism to recover the true caller identity.

## Impact Explanation

A non-allowlisted user can trade on a curated pool that is supposed to restrict access to specific counterparties (e.g., KYC'd addresses, whitelisted market makers). The bypass is unconditional once the router is allowlisted, requires no special privileges, and is reachable through the standard public periphery path. This constitutes a direct break of the pool admin's curation policy and, depending on the pool's purpose, exposes LPs to trades with unintended counterparties or allows extraction of value from pools designed for closed participant sets. This matches the "Allowlist path" smart audit pivot: "swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through router."

## Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and also wants to support `MetricOmmSimpleRouter` faces this issue. The router is the primary public swap entrypoint in the periphery. A pool admin who allowlists the router (the only way to make the router work) immediately opens the pool to all users. The trigger requires no special timing, no privileged role, and no unusual token behavior — a single `exactInputSingle` call suffices.

## Recommendation

The extension must verify the actual end-user, not the intermediary. Two viable approaches:

1. **Pass user identity through `extensionData`:** Have the router encode `msg.sender` into `extensionData` for each hop, and have `SwapAllowlistExtension.beforeSwap` decode and verify it. The extension must also verify that the encoding came from a trusted router (e.g., by checking `sender` is a factory-registered router).

2. **Distinguish direct calls from router calls:** The extension can check whether `sender` is a known router; if so, decode the actual user from `extensionData`; otherwise treat `sender` as the user directly.

Note: `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position owner explicitly passed as a separate argument), not on `sender`.

## Proof of Concept

```solidity
// Pool configured with SwapAllowlistExtension.
// Pool admin allowlists the router so router-based swaps work.
// allowedSwapper[pool][router] = true
// allowedSwapper[pool][alice]  = true   (intended allowlisted user)
// allowedSwapper[pool][bob]    = false  (non-allowlisted user)

// Bob (non-allowlisted) calls the router:
router.exactInputSingle(ExactInputSingleParams({
    pool: curated_pool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));

// Inside the pool:
//   _beforeSwap(msg.sender=router, recipient=bob, ...)
//   SwapAllowlistExtension.beforeSwap(sender=router, ...)
//   allowedSwapper[pool][router] == true  → passes
//
// Bob's swap executes successfully despite not being allowlisted.
```

**Key code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
