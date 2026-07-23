Audit Report

## Title
SwapAllowlistExtension Per-User Allowlist Bypassed When Router Is Allowlisted — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract address, not the originating EOA. This creates two mutually exclusive failure modes: either the router is not allowlisted (blocking all router-mediated swaps, even for individually allowlisted users), or the router is allowlisted (granting every user — including non-allowlisted ones — the ability to bypass the gate by routing through the router).

## Finding Description
**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

**Step 2 — The extension checks `sender` against the per-pool allowlist.**

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool) as the mapping key and `sender` (the pool's direct caller) as the identity to check: [2](#0-1) 

**Step 3 — The router calls `pool.swap` directly, making itself the `msg.sender`.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` with no mechanism to forward the original EOA: [3](#0-2) 

The same pattern holds for `exactInput` (L104-112), `exactOutputSingle` (L136-137), and `exactOutput` (L165-181). In all cases the router is `msg.sender` to the pool, and the original EOA is never forwarded. [4](#0-3) 

**Why existing guards fail:**

The `allowedSwapper` mapping is keyed by `[pool][sender]` where `sender` is the pool's direct caller. There is no secondary check, no `extensionData` parsing, and no router-awareness in the extension. The `allowAllSwappers` flag is the only escape hatch, but enabling it removes all access control entirely. [5](#0-4) 

## Impact Explanation
A pool deploying `SwapAllowlistExtension` intends to restrict trading to a curated set of addresses (e.g., KYC-gated, institutional, or market-maker-only pools). Once the admin allowlists the router to support normal swap UX, the allowlist is rendered entirely ineffective: any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps on the restricted pool. This is an admin-boundary break — an unprivileged path (router → pool) circumvents a configured access control guard. The extension decision value (`allowedSwapper[pool][sender]`) evaluates to `true` for the router address rather than the actual swapper, corrupting the access control outcome.

## Likelihood Explanation
The router is the standard user-facing entry point for swaps. Any pool that wants to support normal swap UX must allowlist the router. The moment the admin does so, the bypass is live for all users. The trigger is a routine, expected admin action. No special attacker capability is required — any EOA can call the router.

## Recommendation
The extension must resolve the original swapper identity rather than trusting the immediate `pool.swap` caller. Two sound approaches:

1. **Pass original sender via `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires coordinated changes to the router and extension.
2. **Router registry with `extensionData` forwarding**: The extension maintains a set of trusted routers; when `sender` is a trusted router, it reads the actual swapper from a standardized field in `extensionData`.

Additionally, document clearly that allowlisting the router grants swap access to all router users, not individual EOAs.

## Proof of Concept
```solidity
// Pool configured with SwapAllowlistExtension.
// Admin allowlists the router so that allowlisted users can swap via UX.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not individually allowlisted) calls through the router.
// The extension sees sender = address(router), which IS allowlisted → passes.
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Swap succeeds despite attacker not being on the per-user allowlist.
// beforeSwap evaluates allowedSwapper[pool][router] == true and passes.
```

The `beforeSwap` check at line 37 evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is `address(router)` — not the attacker's EOA — so the check passes. [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
