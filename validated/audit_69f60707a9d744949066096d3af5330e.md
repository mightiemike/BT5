Audit Report

## Title
`SwapAllowlistExtension` Gates on Router Address Instead of End-User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. A pool admin who allowlists the router inadvertently grants swap access to every user, including those not on the per-user allowlist.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check against the `sender` parameter:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← this is the router when called via MetricOmmSimpleRouter
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that `sender` value directly to the extension: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)`. At that point `msg.sender` inside the pool is the router contract address, so `sender` forwarded to the extension is the router — not the end-user: [4](#0-3) 

The allowlist mapping is keyed `allowedSwapper[pool][swapper]`: [5](#0-4) 

For router-originated swaps the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The pool admin faces a binary choice: allowlist the router (every user can swap through it) or do not allowlist the router (no user can swap through it, even allowlisted ones). There is no configuration that restricts router-based swaps to a specific set of users. No existing guard in the extension or pool corrects for this — the extension has no access to the original `msg.sender` of the router call, and the router stores the payer only in transient storage for its own callback, not in any field the extension can query.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, protocol-owned accounts) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The attacker executes swaps against the pool's liquidity without authorization, receiving output tokens at oracle-derived prices the pool admin intended to expose only to trusted parties. This constitutes a direct loss of LP principal and a broken core pool invariant (the allowlist access control). This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break" impact criteria.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary documented user-facing swap entry point. Any user who observes that a pool uses `SwapAllowlistExtension` and that the router is allowlisted can trivially bypass the per-user restriction with a single `exactInputSingle` call. No special privileges, flash loans, or multi-step setup are required. The condition (router allowlisted by admin intending to enable router-based swaps for their allowlisted users) is a natural and expected admin action, making exploitation highly likely in practice.

## Recommendation

The pool's `swap` interface does not carry an originator/payer field distinct from `msg.sender`. The cleanest fix is to encode the real end-user address in `extensionData` at the router level and have the extension decode and verify it — but this requires the extension to trust that the pool faithfully forwards `extensionData` (which it does, as confirmed in `ExtensionCalling._beforeSwap`). Alternatively, the pool interface could be extended with a separate `originator` field analogous to how `addLiquidity` separates `sender` (payer) from `owner` (position beneficiary). Until then, `SwapAllowlistExtension` must document that it gates the direct caller of `pool.swap()`, not the end-user, and pool admins must not rely on it for per-user access control when any intermediary contract (router, multicall, etc.) is in use.

## Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured as a beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to allow their allowlisted users to trade via the router.
3. Attacker (address NOT in allowedSwapper[pool]) calls:
       MetricOmmSimpleRouter.exactInputSingle(pool, tokenIn, tokenOut, amountIn, ...)
4. Router calls pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData).
5. MetricOmmPool.swap passes msg.sender (= router) to _beforeSwap as `sender`.
6. Extension checks allowedSwapper[pool][router] == true → passes.
7. Swap executes; attacker receives output tokens.
   Per-user allowlist is completely bypassed.

Foundry test sketch:
- Deploy SwapAllowlistExtension, pool, and MetricOmmSimpleRouter.
- Admin calls setAllowedToSwap(pool, address(router), true).
- Prank as attacker (not in allowlist), call router.exactInputSingle(...).
- Assert swap succeeds and attacker receives tokens despite not being allowlisted.
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
