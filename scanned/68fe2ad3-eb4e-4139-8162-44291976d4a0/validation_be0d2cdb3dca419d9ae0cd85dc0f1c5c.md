### Title
`SwapAllowlistExtension` Allowlist Fully Bypassed When Router Is Allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates on `sender`, which is `msg.sender` of the direct `pool.swap()` caller. When any user routes through `MetricOmmSimpleRouter`, the pool sees `sender = router`. If the pool admin allowlists the router (the only way to let allowlisted users use the router), every unprivileged address can bypass the allowlist entirely by routing through the same public contract.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension caller). `sender` is the value the pool passes as the first argument to `_beforeSwap`, which is always `msg.sender` of `pool.swap()`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient, zeroForOne, amountSpecified,
    priceLimitX64, packedSlot0Initial,
    bidPriceX64, askPriceX64, extensionData
);
```

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly, making the router the `msg.sender` the pool sees:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The pool admin faces an inescapable dilemma:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every address on-chain can bypass the allowlist via the router |

There is no mechanism in the router to forward the originating user's identity to the extension. The extension has no way to distinguish between different users who arrive through the same router address.

The same bypass applies to multi-hop `exactInput` (all hops use `msg.sender = router`) and to `exactOutput` (intermediate hops inside `_exactOutputIterateCallback` also call `pool.swap()` from the router context).

### Impact Explanation

Any unprivileged address can swap on a pool whose admin intended to restrict access to a specific allowlist (e.g., KYC'd counterparties, institutional LPs, or whitelisted market makers). The allowlist guard is rendered completely ineffective the moment the router is allowlisted. Depending on pool configuration, this enables:

- Unauthorized parties to drain one-sided liquidity at oracle-quoted prices from a restricted pool.
- Circumvention of regulatory or contractual access controls the pool admin believed were enforced on-chain.

This is a direct loss-of-principal path for LPs who deposited under the assumption that only trusted counterparties could swap against their liquidity.

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router. This is the natural and expected operational step: without it, no allowlisted user can use the router, making the router useless for that pool. Any pool that deploys `SwapAllowlistExtension` and also wants router UX for its allowlisted users will trigger this condition. The attacker needs no special privilege — a single public `exactInputSingle` call suffices.

### Recommendation

The extension must gate on the **economically relevant actor**, not the intermediary. Two sound approaches:

1. **Check `sender` only for direct calls; require the router to forward the originating user.** Add an `extensionData`-encoded `address realSender` field that the router populates, and verify it in the extension. The extension then checks `allowedSwapper[pool][realSender]` when `sender == router`.

2. **Mirror the `DepositAllowlistExtension` pattern.** `DepositAllowlistExtension` correctly gates on `owner` (the economically relevant party), not `sender`. For swaps, introduce a `swapper` field in `extensionData` that the router always sets to `msg.sender` before calling the pool, and gate on that field instead of the raw `sender`.

Either approach breaks the router-as-identity conflation and restores the intended per-user access control.

### Proof of Concept

```
Setup:
  pool deployed with SwapAllowlistExtension
  admin: setAllowedToSwap(pool, alice, true)       // alice is KYC'd
  admin: setAllowedToSwap(pool, router, true)      // needed so alice can use the router

Attack (bob, not allowlisted):
  bob calls router.exactInputSingle({
      pool:      pool,
      recipient: bob,
      zeroForOne: true,
      amountIn:  X,
      ...
  })

  router calls pool.swap(bob, true, X, ...)
    → pool calls _beforeSwap(msg.sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  ✓
    → swap executes, bob receives tokens

Result: bob, who is not on the allowlist, successfully swaps against the restricted pool.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
