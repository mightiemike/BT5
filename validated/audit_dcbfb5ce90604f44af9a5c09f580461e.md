Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of actual user, allowing any caller to bypass per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's `msg.sender` — the router contract when a user routes through `MetricOmmSimpleRouter`. The extension therefore evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. Any pool admin who allowlists the router (the only way to permit router-mediated swaps for legitimate users) simultaneously opens the gate to every address, including those explicitly excluded from the allowlist.

## Finding Description
`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient, ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension, and `SwapAllowlistExtension.beforeSwap` checks it against its per-pool mapping:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

At this point `msg.sender` inside the pool is the **router**, so `sender` delivered to the extension is the router address. The extension evaluates `allowedSwapper[pool][router]`, never touching the actual user's address. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap` directly with the router as `msg.sender`. [4](#0-3) 

There is no existing guard that recovers the original caller: `extensionData` is passed through unchanged from the user's call parameters and is not authenticated, and the extension has no access to transient storage where the router stores the real payer. [5](#0-4) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd or institutional participants) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized swappers can execute swaps against the restricted pool, draining LP positions at oracle prices and interacting with pools that were contractually or regulatorily required to be closed to them. This constitutes a broken core pool invariant (access control) and a direct loss of LP assets, meeting the contest threshold for a High severity finding.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical, publicly deployed periphery swap entry point. Any pool admin who wants allowlisted users to be able to use the router must call `setAllowedToSwap(pool, router, true)` — there is no other mechanism. The moment that entry is added, the bypass is live for every address. No special privilege, flash loan, or unusual token behavior is required; a standard `exactInputSingle` call suffices and is repeatable by any EOA or contract.

## Recommendation
The extension must recover the **original user** from the call context. Two concrete options:

1. **Router encodes initiator in `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. Requires a convention between the router and the extension, and the extension must distinguish router calls from direct calls.

2. **Router-aware allowlist with authenticated identity**: Introduce a separate `allowedRouter` mapping; when `sender` is a known router, require `extensionData` to carry a signed or otherwise authenticated user identity that the extension verifies.

The simplest safe default is to treat any call whose `sender` is not in `allowedSwapper` as blocked, and document that router-mediated swaps require the router to be allowlisted — with an explicit warning that doing so opens the gate to all users.

## Proof of Concept
```
Setup:
  - Pool P with SwapAllowlistExtension E
  - Admin allowlists alice: setAllowedToSwap(P, alice, true)
  - Admin allowlists router R so alice can use it: setAllowedToSwap(P, R, true)
  - bob is NOT allowlisted

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
    → router calls P.swap(recipient, zeroForOne, ...)
      msg.sender inside pool = router R
    → pool calls _beforeSwap(sender=R, ...)
    → extension checks allowedSwapper[P][R] == true  ✓
    → swap executes; bob receives output tokens

Result:
  bob, an explicitly excluded address, successfully swaps against the
  restricted pool. The allowlist invariant is broken.

Foundry test outline:
  1. Deploy pool with SwapAllowlistExtension.
  2. setAllowedToSwap(pool, alice, true).
  3. setAllowedToSwap(pool, router, true).
  4. vm.prank(bob); router.exactInputSingle({pool: pool, ...});
  5. Assert swap succeeds (no NotAllowedToSwap revert).
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
