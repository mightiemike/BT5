Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Original Swapper, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` — the immediate caller of the pool. When swaps are routed through `MetricOmmSimpleRouter`, the router becomes `msg.sender` at the pool level. If the pool admin allowlists the router to support router-mediated swaps for legitimate users, any unprivileged user can bypass the allowlist entirely by calling the public router.

## Finding Description

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

This means when `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool receives the router as `msg.sender` and forwards it as `sender` to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks: [3](#0-2) 

`msg.sender` here is the pool (correct), but `sender` is the router — not the original user. The extension's `allowedSwapper[pool][sender]` check evaluates whether the **router** is allowlisted, not whether the actual end-user is. Any user who calls the public router bypasses the per-user allowlist check entirely.

The dilemma is structural: if the router is not allowlisted, legitimate allowlisted users cannot use the router at all. If the router is allowlisted to support them, every unprivileged user gains access.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to enforce access control (e.g., KYC compliance, institutional-only venues, curated LP pools) is rendered completely ineffective. Any unprivileged user can execute swaps against restricted pool liquidity by routing through `MetricOmmSimpleRouter`. This constitutes broken core pool functionality with direct fund-impacting consequences: disallowed users can trade against restricted liquidity, violating the pool's intended access invariant.

## Likelihood Explanation

No special preconditions, privileged access, or rare on-chain state are required. `MetricOmmSimpleRouter` is a standard publicly deployed periphery contract. Any user who observes that a pool uses `SwapAllowlistExtension` and that the router is allowlisted (discoverable by calling `isAllowedToSwap`) can immediately exploit this by calling the router instead of the pool directly. The attack is repeatable and costless beyond gas. [4](#0-3) 

## Recommendation

The extension must gate the **original user**, not the immediate pool caller. The pool's `swap` function should accept an explicit `originalSender` parameter set by the router to `msg.sender` before calling the pool, and forward that to the extension hook instead of `msg.sender`. Alternatively, the pool can pass `tx.origin` as a fallback, though the explicit parameter approach is cleaner and avoids `tx.origin` risks. At minimum, documentation must state that pools using `SwapAllowlistExtension` must not allowlist the router, and that router-mediated swaps are incompatible with this extension — but this breaks intended UX.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, address(router), true)
    so that allowlisted users can use the router
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle(
       pool=restrictedPool, recipient=attacker, ...
     )
  2. Router calls pool.swap(recipient=attacker, ...)
     → pool's msg.sender = address(router)
  3. Pool calls _beforeSwap(msg.sender=router, ...)
  4. Extension checks: allowedSwapper[pool][router] == true ✓
  5. Swap executes — attacker traded against restricted pool liquidity

Result:
  - allowedSwapper[pool][attacker] == false (never set)
  - attacker bypassed the check via the public router
  - Any user can repeat this; the allowlist is nullified
``` [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-13)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L27-29)
```text
  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
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
