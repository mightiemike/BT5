Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of end-user, allowing any router caller to bypass per-user swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender`, which the pool sets to `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` at the pool boundary, so the allowlist evaluates the router's address rather than the actual user. A pool admin who allowlists the router to restore router-mediated swaps for legitimate users simultaneously opens the pool to every address that can call the router, fully defeating the per-user gate.

## Finding Description

**Pool passes `msg.sender` as `sender` to the extension:**

In `MetricOmmPool.swap`, `_beforeSwap` is called with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as `sender` to every registered extension: [2](#0-1) 

**Extension checks the wrong identity:**

`SwapAllowlistExtension.beforeSwap` receives `sender` (the direct pool caller) and checks it against the per-pool allowlist: [3](#0-2) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`.

**The router is the direct pool caller:**

`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` directly, making the router `msg.sender` at the pool boundary: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**The identity mismatch creates an impossible configuration:**

| Call path | `sender` seen by extension | Allowlist lookup |
|---|---|---|
| User → `pool.swap()` directly | user address | `allowedSwapper[pool][user]` ✓ |
| User → `MetricOmmSimpleRouter` → `pool.swap()` | **router address** | `allowedSwapper[pool][router]` ✗ |

The pool admin configures individual user addresses: [5](#0-4) 

But when any user routes through `MetricOmmSimpleRouter`, the check becomes `allowedSwapper[pool][router]`. The admin faces an impossible choice:
- **Do not allowlist the router** → all router-mediated swaps revert, including those from legitimately allowlisted users who rely on the router for slippage protection.
- **Allowlist the router** → `allowedSwapper[pool][router] = true` passes for every caller of the router, regardless of whether that caller is individually allowlisted. The per-user gate is completely bypassed.

## Impact Explanation

When the pool admin allowlists the router to restore router functionality for legitimate users, every address that can call `MetricOmmSimpleRouter` can swap on the restricted pool. This constitutes:

- **Compliance boundary break**: Pools deployed with allowlists for regulatory or institutional reasons (KYC/AML gating) are rendered ineffective; any public address can trade.
- **LP principal loss**: LPs who deposited under the assumption that only vetted counterparties would trade against them are exposed to adversarial flow from the general public, enabling unauthorized liquidity extraction.
- **Admin-boundary break**: An unprivileged path (calling the public router) bypasses the pool admin's access control configuration.

This satisfies the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" and "Broken core pool functionality causing loss of funds" impact gates.

## Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless periphery contract. Any user who wants slippage protection or multi-hop routing will naturally use it. A pool admin who deploys a `SwapAllowlistExtension` and observes that allowlisted users cannot swap through the router will add the router to the allowlist as the obvious fix — at which point the bypass is live. No privileged access, exotic token behavior, or special conditions are required; any address that can call the router can exploit this.

## Recommendation

The extension must gate the economically relevant actor — the end-user — not the intermediary contract. Two sound approaches:

1. **Forward the originating user through `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData` before calling `pool.swap`. The extension decodes and checks that address. A signature or trusted-forwarder pattern is required to prevent callers from forging this field.

2. **Check `recipient` instead of `sender`**: If the pool's design guarantees `recipient == actual user`, the extension can check `recipient` (the second argument to `beforeSwap`) instead of `sender`. This is simpler but only correct when the recipient invariant holds.

## Proof of Concept

```
Setup:
  pool deployed with SwapAllowlistExtension
  pool admin: setAllowedToSwap(pool, alice, true)   // alice is allowed
  pool admin: setAllowedToSwap(pool, bob,   false)  // bob is NOT allowed

Direct swap (works correctly):
  bob calls pool.swap(...) directly
  → beforeSwap receives sender = bob
  → allowedSwapper[pool][bob] = false → revert NotAllowedToSwap ✓

Router bypass:
  pool admin calls setAllowedToSwap(pool, router, true)
    (necessary so alice can use the router)
  bob calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  → router calls pool.swap(...)
  → beforeSwap receives sender = router
  → allowedSwapper[pool][router] = true → passes ✗
  → bob successfully swaps on the restricted pool
```

Root cause: `SwapAllowlistExtension.beforeSwap` at the `sender` check: [6](#0-5) 

Combined with the pool's unconditional use of `msg.sender` as the `sender` argument: [7](#0-6)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
