Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is always the router address. A pool admin who allowlists the router to support router-mediated swaps for legitimate users simultaneously grants unrestricted swap access to every address that can call the router, completely defeating the allowlist guard.

## Finding Description

**Hook check:** `SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct namespace) and `sender` is whatever the pool passed as the first argument. [1](#0-0) 

**What the pool passes as `sender`:** `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, so `sender` in the extension is the direct caller of `pool.swap()` — not the originating user. [2](#0-1) 

**What the router passes to the pool:** `exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` directly, making the pool see `msg.sender = router`. The extension therefore receives `sender = router`, never the real user. [3](#0-2) 

**Same mismatch in `exactInput`:** For multi-hop exact-input, the first hop uses `msg.sender` only as the *payer* stored in transient storage; the pool still sees `msg.sender = router` for every hop. [4](#0-3) 

**Same mismatch in `exactOutput`:** The recursive `_exactOutputIterateCallback` also calls `pool.swap(...)` from the router context, so every hop presents `sender = router` to the extension. [5](#0-4) 

**Contrast with `DepositAllowlistExtension`:** The deposit hook ignores `sender` (first arg) and checks `owner` (second arg) — the economically relevant actor. `SwapAllowlistExtension` has no equivalent fallback; it only checks `sender`. [6](#0-5) 

**Exploit path:**
1. Pool admin deploys pool with `SwapAllowlistExtension`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — alice is the intended allowlisted user.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — intended to let alice reach the pool through the standard periphery.
4. Charlie (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, zeroForOne, amount, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Charlie's swap settles against the curated pool, bypassing the allowlist entirely. [7](#0-6) 

## Impact Explanation

Any non-allowlisted user can trade on a pool designed to be permissioned by routing through `MetricOmmSimpleRouter`. The allowlist curation guarantee is completely broken for all router-mediated swaps, exposing LP funds to unauthorized counterparties and violating the core access-control invariant of the extension. This constitutes a broken core pool functionality causing direct loss of the curation guarantee and potential fund exposure, meeting the High severity threshold. [1](#0-0) 

## Likelihood Explanation

The trigger requires only that the pool admin allowlists the router — a routine and expected operational step for any curated pool that intends to support the standard periphery. No privileged access, no special tokens, and no complex setup is needed by the attacker. Any EOA can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router pointing at the curated pool. The condition is virtually certain to be met in production. [8](#0-7) 

## Recommendation

The `beforeSwap` hook must gate the **original user**, not the intermediary router. Two viable approaches:

1. **Extension-data forwarding:** The router encodes `msg.sender` into `extensionData` for each hop; the extension decodes and checks that address. The pool already forwards `extensionData` verbatim to the hook, and the router already accepts per-hop `extensionData` from callers.
2. **Dedicated `originalSender` field:** Add an `originalSender` field to the swap path or extension payload that the router always populates with `msg.sender`, and have the extension validate that field instead of (or in addition to) `sender`.

The fix must be applied consistently across `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` (including the recursive callback path). [4](#0-3) 

## Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension.
2. setAllowedToSwap(pool, alice, true)
   → allowedSwapper[pool][alice] = true
3. setAllowedToSwap(pool, router, true)
   → allowedSwapper[pool][router] = true
   (intended to let alice reach the pool through the router)

Attack
──────
4. Charlie (not allowlisted) calls:
     router.exactInputSingle({pool: pool, recipient: charlie, ...})

5. Router calls pool.swap(charlie, zeroForOne, amount, ...) with msg.sender = router.

6. Pool calls _beforeSwap(router, ...).

7. Extension evaluates:
     allowAllSwappers[pool]           → false
     allowedSwapper[pool][router]     → true  ← router is allowlisted
   → no revert; swap proceeds.

8. Charlie's swap settles against the curated pool, bypassing the allowlist.

Foundry test sketch
───────────────────
function test_swapAllowlistBypass() public {
    // setup: pool with SwapAllowlistExtension, alice and router allowlisted
    vm.prank(poolAdmin);
    ext.setAllowedToSwap(pool, alice, true);
    vm.prank(poolAdmin);
    ext.setAllowedToSwap(pool, address(router), true);

    // charlie is NOT allowlisted; routes through the router
    vm.prank(charlie);
    uint256 out = router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: pool, recipient: charlie, zeroForOne: true,
            amountIn: 1e18, amountOutMinimum: 0,
            priceLimitX64: 0, deadline: block.timestamp, extensionData: ""
        })
    );
    // swap succeeds — charlie bypassed the allowlist
    assertGt(out, 0);
}
``` [7](#0-6) [9](#0-8) [3](#0-2)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-80)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-39)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
```
