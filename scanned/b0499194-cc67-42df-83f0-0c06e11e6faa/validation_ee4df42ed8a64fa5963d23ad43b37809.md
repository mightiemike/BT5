The core of this question is traceable through three files. Let me confirm the exact call chain:

The call chain is now fully traced. Here is the determination:

---

### Title
Router Address Substitution Defeats Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool. The pool always passes `msg.sender` (i.e., the direct caller of `pool.swap`) as `sender`. When `MetricOmmSimpleRouter.exactInput` is used, the direct caller of every `pool.swap` is the **router**, not the originating EOA. If the router is allowlisted for a pool, every EOA that routes through it bypasses the per-user allowlist on every hop.

### Finding Description

**Step 1 — Router calls pool.swap with itself as msg.sender.**

In `exactInput`, the router calls `pool.swap(...)` directly: [1](#0-0) 

For hops `i > 0`, the payer is `address(this)` (the router), and the router is always the `msg.sender` to the pool.

**Step 2 — Pool passes msg.sender as sender to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`: [2](#0-1) 

`msg.sender` here is the router address, not the originating EOA.

**Step 3 — Extension checks the router, not the EOA.**

`SwapAllowlistExtension.beforeSwap` receives `sender = router` and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [3](#0-2) 

`msg.sender` here is the pool; `sender` is the router. The check is `allowedSwapper[pool][router]`, never `allowedSwapper[pool][EOA]`.

**Result:** A pool admin who wants to allow the official router while restricting direct pool access to specific EOAs will allowlist the router. Once the router is allowlisted, **any** EOA can call `exactInput` through the router and pass the allowlist check on every hop, because the extension always sees the router as the swapper.

**Correction on the extensionData claim:** The `extensionData` parameter in `beforeSwap` is unnamed and completely ignored by `SwapAllowlistExtension`. It cannot influence the allowlist decision and does not corrupt `allowedSwapper` state. The vulnerability is purely the router address substitution, not extensionData manipulation.

### Impact Explanation

The `SwapAllowlistExtension` is documented as "Gates `swap` by swapper address, per pool." [4](#0-3) 

When the router is used, this invariant is broken: the extension cannot distinguish between different EOAs routing through the same router. Any non-allowlisted EOA can swap on any pool in the route as long as the router is allowlisted. This is broken core functionality of the extension — the per-user gating it is designed to enforce is unenforceable via the router path.

### Likelihood Explanation

The router is the primary intended interface for multi-hop swaps. A pool admin who wants to allow router-based trading while restricting direct pool access is a natural and expected configuration. Allowlisting the router is the only way to enable router-based swaps, so this misconfiguration is likely in any deployment that uses both the router and the allowlist extension together.

### Recommendation

`SwapAllowlistExtension.beforeSwap` should check the `recipient` parameter (the ultimate beneficiary) or require the router to forward the originating user's address via `extensionData`. Alternatively, the extension should explicitly document that it is incompatible with router-based flows and that allowlisting the router grants access to all router users.

### Proof of Concept

1. Deploy two pools, each with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` on both pools (allowlisting the router, not any EOA).
3. Non-allowlisted EOA calls `router.exactInput(...)` with a two-hop route through both pools.
4. Both `beforeSwap` calls check `allowedSwapper[pool][router]` → `true` → pass.
5. Swap succeeds for the non-allowlisted EOA.
6. Same EOA calling `pool.swap(...)` directly reverts with `NotAllowedToSwap` on each pool (since `allowedSwapper[pool][EOA]` is false).

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-12)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
