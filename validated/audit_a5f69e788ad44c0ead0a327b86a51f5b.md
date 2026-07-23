Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is the entity that calls `pool.swap()`, so `sender` resolves to the router address rather than the end-user. A pool admin who allowlists the router (required for any router-mediated swap) inadvertently grants every unprivileged user the ability to bypass the per-user allowlist.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the check at L37:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which the pool populates with its own `msg.sender` — i.e., whoever called `pool.swap()`. [2](#0-1) 

In `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `IMetricOmmPoolActions(params.pool).swap(...)` directly, making the router the `msg.sender` to the pool: [3](#0-2) 

The same applies to `exactOutputSingle()`: [4](#0-3) 

The pool's `swap()` interface accepts `extensionData` but the router passes `params.extensionData` directly from the caller without encoding the original `msg.sender` into it. There is no field carrying the original end-user identity to the extension layer. This creates a binary, irreconcilable choice for the pool admin: allowlist the router (every user can swap, per-user allowlist nullified) or do not allowlist the router (no user can ever swap through the router on this pool).

## Impact Explanation
`SwapAllowlistExtension` is the production mechanism for restricting swap access to specific counterparties (e.g., KYC'd traders, institutional desks). Bypassing it allows arbitrary unprivileged users to execute swaps against a pool whose LP positions were sized and priced under the assumption of a trusted, restricted counterparty set. Toxic or adversarial flow from non-allowlisted users can drain LP value through adverse selection, directly reducing token balances owed to LPs on removal. This is a broken core pool functionality / LP asset loss path meeting Sherlock medium/high thresholds.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical, publicly deployed periphery entry point. Any pool admin who wants allowlisted users to interact via the standard UI/router must allowlist the router address. This is the expected operational path, making the precondition realistic and the bypass trivially reachable by any unprivileged caller with no special permissions required.

## Recommendation
The extension must gate the original end-user, not the intermediary. The most practical fix: the router encodes `msg.sender` into `extensionData` before forwarding to the pool (e.g., appending `abi.encode(msg.sender)`). `SwapAllowlistExtension` then decodes and checks that address when `sender` is a known router. The extension should verify `msg.sender` (the pool) is a known factory pool before trusting the decoded identity to prevent spoofing via a malicious pool.

## Proof of Concept
```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // required for any router swap
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)

Attack:
  1. alice (non-allowlisted) calls:
       router.exactInputSingle({pool: pool, tokenIn: ..., ...})
  2. Router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
       → msg.sender to pool = router address
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension evaluates: allowedSwapper[pool][router] == true  ✓
  5. Swap executes. Alice swaps successfully despite not being on the allowlist.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L136-137)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
