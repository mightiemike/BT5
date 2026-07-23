All four cited files are confirmed in the repository. The code matches the claim exactly:

- `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap` [1](#0-0) 
- `ExtensionCalling._beforeSwap` forwards `sender` unchanged to every extension [2](#0-1) 
- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — pool as key, caller-of-swap as swapper [3](#0-2) 
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` [4](#0-3) 
- Same pattern in `exactInput`, `exactOutputSingle`, `exactOutput` [5](#0-4) 

---

Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Enabling Full Per-User Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is set to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks whether the router is allowlisted rather than whether the individual user is allowlisted. Any user can bypass a per-user swap allowlist on any pool that has allowlisted the router by simply calling any of the router's `exact*` entry points.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient, ...
);
```

`ExtensionCalling._beforeSwap` encodes that value as `sender` and forwards it unchanged to every configured extension via `_callExtensionsInOrder`. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) is used, the router calls `pool.swap()` directly, making the router the `sender` seen by the extension. The extension therefore evaluates `allowedSwapper[pool][router]` — a single binary flag for the entire router contract — rather than `allowedSwapper[pool][actualUser]`. The per-user allowlist is structurally unreachable for any router-mediated swap. No existing guard in the extension, pool, or router recovers or validates the originating user address.

## Impact Explanation
A pool admin who deploys a pool with `SwapAllowlistExtension` intends to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties). To make the standard periphery router usable on the pool, the admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, every Ethereum address can bypass the per-user restriction by calling any of the router's `exact*` entry points. The allowlist provides zero protection against router-mediated swaps. Unauthorized users can trade against LP positions in a pool explicitly configured to exclude them, directly violating the pool admin's curation policy and any downstream compliance or risk-management assumptions. This is a broken core pool access-control invariant: the swap allowlist check does not cover the exact actor intended and is bypassed through the standard router path, matching the "Allowlist path" audit pivot.

## Likelihood Explanation
The bypass requires no special privilege, no flash loan, and no contract deployment. Any user who knows the pool uses a swap allowlist simply calls `MetricOmmSimpleRouter.exactInputSingle` instead of `pool.swap` directly. The router is the standard, documented periphery entry point exercised by every normal user of the protocol. The only precondition is that the pool admin has allowlisted the router — which is the only way to make the router usable at all on an allowlisted pool, so the admin is forced into the vulnerable configuration. The attack is trivially repeatable by any address.

## Recommendation
Pass the original user's address through the router to the pool and have the pool forward it to the extension as `sender`. One approach: the router encodes the real `msg.sender` in `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks it when `msg.sender` (the pool) indicates a trusted router. A cleaner approach: add a `payer`/`originator` field to the swap call that the pool passes to extensions separately from `msg.sender`. At minimum, document that `SwapAllowlistExtension` cannot enforce per-user restrictions when the router is used, and provide a router-aware variant that recovers the originator from a signed payload or trusted forwarder pattern.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls: setAllowedToSwap(pool, router, true)       // enable router
  pool admin calls: setAllowedToSwap(pool, alice, true)        // KYC alice
  pool admin does NOT allowlist bob

Attack (bob bypasses allowlist):
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)          // msg.sender = router
    → pool calls _beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router]   // true → passes
    → swap executes for bob despite bob not being allowlisted

Direct call (correctly blocked):
  bob calls pool.swap(recipient, ...)                 // msg.sender = bob
    → pool calls _beforeSwap(sender=bob, ...)
    → extension checks allowedSwapper[pool][bob]      // false → reverts NotAllowedToSwap
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, allowlist the router but not `bob`, call `router.exactInputSingle` from `bob`, assert the swap succeeds; then call `pool.swap` directly from `bob` and assert it reverts with `NotAllowedToSwap`.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
