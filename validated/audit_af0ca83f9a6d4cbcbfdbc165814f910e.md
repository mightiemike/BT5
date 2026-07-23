All three code paths are confirmed in the repository. The claim is fully supported:

- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the immediate caller of `pool.swap()` [1](#0-0) 
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` to the pool [2](#0-1) 
- `ExtensionCalling._beforeSwap` encodes and forwards `sender` (the router) to the extension [3](#0-2) 

---

Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Allowlist Guard — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded from the pool, which is the immediate caller of `pool.swap()`. When `MetricOmmSimpleRouter` mediates a swap, it is the immediate caller, so the extension checks whether the **router** is allowlisted rather than the end-user. Any non-allowlisted user can bypass the allowlist by routing through the router if the router itself is allowlisted — which is the expected operational configuration for any pool that accepts router-mediated trades.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`ExtensionCalling._beforeSwap` encodes and forwards the `sender` parameter (which the pool sets to its own `msg.sender`) to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
  )
);
```

**Step 2 — Router calls `pool.swap()` directly, making itself `msg.sender`.**

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` with no originator field:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

The router is therefore `msg.sender` to the pool, and the pool passes the router address as `sender` to `_beforeSwap`.

**Step 3 — Extension checks the router address, not the user.**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool, `sender` = router. The lookup is `allowedSwapper[pool][router]`. The actual end-user's address is never consulted.

**Existing guards are insufficient.** There is no mechanism in the pool, router, or extension to thread the original EOA through to the extension. The `extensionData` bytes field is user-controlled and cannot be trusted as an originator proof.

## Impact Explanation

A pool admin deploys a curated pool (e.g., KYC-gated or institutional-only) and attaches `SwapAllowlistExtension`. To allow approved users to trade via the router, the admin calls `setAllowedToSwap(pool, router, true)`. From that moment, every address on the network can trade on the pool by calling any router function — the allowlist is completely inoperative. Non-approved users receive the same execution as approved ones, violating the pool admin's explicit access-control policy. This constitutes a broken core pool functionality causing loss of access-control integrity and potential fund exposure under conditions the pool admin explicitly prohibited. Severity: **High**.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Pool admins who want approved users to trade via the router must allowlist the router address — this is the expected operational pattern. The bypass is therefore reachable on every production pool using `SwapAllowlistExtension` that accepts router-mediated swaps. No special permissions, malicious setup, or non-standard tokens are required; any EOA can call `exactInputSingle` on the router pointing at the guarded pool.

## Recommendation

The extension must gate the economically relevant actor — the end-user — not the immediate caller of `pool.swap()`. The cleanest fix is to thread the original `msg.sender` from the router through to the extension as a distinct `originator` field, using transient storage (already used for reentrancy guards in the router base) so the router records `msg.sender` before calling the pool, and the pool forwards it as a separate argument to the extension. Alternatively, document and enforce that `SwapAllowlistExtension` is incompatible with router-mediated swaps and revert in `beforeSwap` when `sender` is a known router address.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension (beforeSwap hook enabled)
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  1. attacker (non-allowlisted EOA) calls:
       router.exactInputSingle({pool: pool, recipient: attacker, ...})
  2. Router calls pool.swap(attacker, ...) — msg.sender to pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true ✓
  5. Swap executes successfully for the non-allowlisted attacker

Result:
  - Non-allowlisted user completes a swap on a curated pool
  - SwapAllowlistExtension guard is fully bypassed
  - Pool admin's access-control policy is silently violated
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
