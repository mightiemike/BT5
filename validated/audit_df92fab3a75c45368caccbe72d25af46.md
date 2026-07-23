Audit Report

## Title
SwapAllowlistExtension Gates Router Address Instead of Actual Swapper, Allowing Any User to Bypass Per-User Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's `msg.sender` — the router contract address when a swap is routed through `MetricOmmSimpleRouter`. The extension checks `allowedSwapper[pool][router_address]` instead of `allowedSwapper[pool][actual_user]`, meaning the allowlist gates the intermediary contract rather than the economic actor. Any unprivileged user can bypass a pool's per-user allowlist by routing through the standard periphery router.

## Finding Description

The call chain is confirmed by the production code:

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)` — `msg.sender` = user.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — pool's `msg.sender` = **router address**. [1](#0-0) 
3. Pool calls `_beforeSwap(msg.sender, ...)` — passes **router address** as `sender`. [2](#0-1) 
4. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router_address]`. [3](#0-2) 

The actual end-user address is never consulted. This creates an irreconcilable dilemma:

- **If the router is NOT allowlisted:** allowlisted users cannot swap through the standard periphery path — the extension reverts on every router-mediated call.
- **If the router IS allowlisted** (the only way to make the pool usable via the router): the allowlist check passes for **every user** who routes through the router, regardless of individual allowlist status. Per-user curation is completely defeated.

The multihop path compounds this: for hops after the first, the router passes `address(this)` as the payer context, so the same router address appears as `sender` on every hop. [4](#0-3) 

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, whitelisted market makers, or protocol-controlled addresses) is fully bypassable by any unprivileged user who calls the pool through `MetricOmmSimpleRouter`. The attacker pays no extra cost beyond normal swap fees. Any token pair in such a pool is exposed to unrestricted trading, violating the pool admin's curation policy. This constitutes a broken core pool functionality (allowlist enforcement) causing potential direct financial loss — LP value leakage to arbitrageurs who should have been excluded, or regulatory/compliance breach with fund-impacting consequences on restricted pools. This matches the admin-boundary break impact gate: an unprivileged path bypasses a pool admin's access control configuration. [5](#0-4) 

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard periphery entry point for all swaps. Any user who reads the protocol docs will use it. No special privileges, flash loans, or unusual conditions are required — a normal `exactInputSingle` call suffices. The pool admin has no on-chain mechanism to distinguish "router called by allowlisted user" from "router called by anyone." The only mitigation available to the admin (not allowlisting the router) makes the pool unusable via the standard interface. Likelihood is **high**: the bypass is reachable on every router-mediated swap to any pool using this extension. [6](#0-5) 

## Recommendation

The `sender` argument passed to `beforeSwap` must represent the economic initiator, not the intermediary. Two complementary fixes:

1. **In the router:** pass the actual user address as an explicit `sender` field in `extensionData`, and have the extension decode it. This requires a protocol-level convention for the extension payload format.
2. **In the extension (preferred, self-contained):** decode the real initiator from `extensionData` when `sender` is a known router, or require pools using this extension to be called directly. Alternatively, gate on `tx.origin` as a last resort (with documented caveats).
3. **Architectural fix:** the pool's `_beforeSwap` should pass both `msg.sender` (the immediate caller) and an optional `initiator` field that the router populates with the real user, so extensions can choose which identity to gate. [7](#0-6) 

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, alice, true)  // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true) // REQUIRED for router to work

Attack:
  - Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) — pool's msg.sender = router
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true → PASSES
  - Bob's swap executes despite not being individually allowlisted.

Alternatively (if admin does NOT allowlist the router):
  - Alice (allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Extension checks allowedSwapper[pool][router] == false → REVERTS
  - Alice cannot use the standard periphery path at all.
``` [2](#0-1) [3](#0-2)

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-13)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
