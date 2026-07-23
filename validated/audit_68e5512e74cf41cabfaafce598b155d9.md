Audit Report

## Title
SwapAllowlistExtension Gates on Router Address Instead of Actual User, Enabling Allowlist Bypass or Permanent Swap Lockout - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter against the per-pool allowlist, but when a user swaps through `MetricOmmSimpleRouter`, the pool receives `msg.sender` = router and forwards that as `sender` to the extension. The extension therefore evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actual_user]`, binding the guard to the wrong actor. This produces two mutually exclusive failure modes: allowlisted users are permanently locked out of the router path, or if the pool admin allowlists the router to unblock them, every unprivileged address bypasses the curated-pool restriction entirely.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value verbatim and calls each extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool) as the mapping key and the `sender` parameter as the swapper identity: [3](#0-2) 

When a user routes through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` — making `msg.sender` to the pool equal to the router address: [4](#0-3) 

The call chain is: `user → router.exactInputSingle → pool.swap(msg.sender=router) → extension.beforeSwap(sender=router)`. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The admin's per-user allowlist entries are invisible to this check.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` (unnamed first param) and gates on `owner` — the LP position owner — which is passed directly by the pool and is not subject to intermediary substitution: [5](#0-4) 

The admin-facing setters correctly gate on `onlyPoolAdmin` but the enforcement point reads the wrong identity, so the admin's intent is never realized on the router path: [6](#0-5) 

## Impact Explanation
**Failure Mode A – Permanent lockout of allowlisted users.** The pool admin allowlists `user_A` by address. `user_A` swaps through the router. The extension sees `sender = router`, finds no entry, and reverts with `NotAllowedToSwap`. The router path is permanently unusable for every individually-allowlisted user, breaking the core swap flow for the pool.

**Failure Mode B – Full allowlist bypass.** To unblock legitimate users, the pool admin allowlists the router address. Now `allowedSwapper[pool][router] = true`, so the check passes for every caller regardless of identity. Any unprivileged address can swap in the curated pool by routing through `MetricOmmSimpleRouter`, defeating the entire curation policy and allowing unauthorized counterparties to trade against LP positions in a pool explicitly configured to restrict access.

Both outcomes are fund-impacting: Mode A makes the pool's swap flow unusable for its intended participants; Mode B allows unauthorized counterparties to trade against LP positions in a pool that was explicitly configured to restrict access. This matches the allowed impact of "Broken core pool functionality causing loss of funds or unusable swap flows" and "Admin-boundary break: pool admin's access control bypassed by an unprivileged path."

## Likelihood Explanation
The router is the primary user-facing swap interface. Any pool that enables `SwapAllowlistExtension` and expects users to interact via the router will immediately hit one of the two failure modes. No special timing, flash loan, or privileged access is required — a normal swap call through the router is sufficient to trigger either outcome. Likelihood is high.

## Recommendation
The extension must resolve the actual end-user identity rather than the immediate caller. Two sound approaches:

1. **Check `recipient` instead of `sender`.** If the protocol's invariant is that the economic beneficiary of a swap is the `recipient`, gate on `recipient` rather than `sender`. This mirrors the pattern used by `DepositAllowlistExtension`, which correctly gates on `owner` rather than `sender`.

2. **Pass the real user explicitly via `extensionData`.** Add a `swapper` field to `extensionData` that the router populates with `msg.sender` before calling the pool, and have the extension decode and verify it. This requires a trusted router convention and validation that the caller is a known router.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  router is NOT allowlisted

Attack (Failure Mode A – lockout):
  alice calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  → router calls pool.swap(recipient=alice, ...)
       msg.sender to pool = router
  → pool calls extension.beforeSwap(sender=router, ...)
       check: allowedSwapper[pool][router] == false
       check: allowAllSwappers[pool] == false
  → revert NotAllowedToSwap
  alice cannot swap despite being explicitly allowlisted.

Attack (Failure Mode B – bypass):
  admin calls setAllowedToSwap(pool, router, true)  // to unblock alice
  bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  → router calls pool.swap(recipient=bob, ...)
       msg.sender to pool = router
  → pool calls extension.beforeSwap(sender=router, ...)
       check: allowedSwapper[pool][router] == true  ✓ passes
  → swap executes for bob despite bob never being allowlisted
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-25)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }
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
