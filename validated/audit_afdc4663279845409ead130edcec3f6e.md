Audit Report

## Title
`SwapAllowlistExtension.beforeSwap()` Checks Router Address Instead of End User, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension` gates swaps per pool by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. Any pool admin who allowlists the router (required for router-based trading) inadvertently opens the pool to every user of the router, completely defeating the allowlist's curation guarantee.

## Finding Description

**Root cause — wrong actor in `beforeSwap`:**

`SwapAllowlistExtension.beforeSwap()` checks:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct key) and `sender` is the first argument passed by the pool to the extension.

**How the pool populates `sender`:**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, passing its own `msg.sender` — whoever called `pool.swap()` — as the `sender` argument: [2](#0-1) 

`ExtensionCalling._beforeSwap()` then forwards this value directly as the first argument to `IMetricOmmExtensions.beforeSwap`: [3](#0-2) 

**How the router calls `pool.swap()`:**

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly with no encoding of the originating user into `extensionData`: [4](#0-3) 

Therefore `msg.sender` inside `pool.swap()` is `address(router)`, not the end user. The extension receives `sender = address(router)` and checks `allowedSwapper[pool][router]`.

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

**Why existing guards are insufficient:**

There is no mechanism in the router to encode the originating user into `extensionData`. The `params.extensionData` field is caller-supplied and passed through verbatim — the router does not inject `msg.sender` into it. The extension has no way to distinguish which end user initiated the call. [6](#0-5) 

## Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading (e.g., to KYC'd users, institutional counterparties, or specific protocol addresses) is fully bypassed. Any unprivileged user can trade against the pool's liquidity by routing through `MetricOmmSimpleRouter`. This constitutes an admin-boundary break: the pool admin's access control is bypassed by an unprivileged path. LP funds are exposed to counterparties the pool admin explicitly excluded, and the pool's risk model is broken. This meets the Sherlock threshold for a High severity finding.

## Likelihood Explanation

The router is the canonical, documented periphery entry point for swaps. Any pool admin who wants allowlisted users to trade via the router must add the router to the allowlist — this is the expected operational pattern, not a misconfiguration. The bypass is triggered by normal, correct admin configuration. Any user who discovers the router is allowlisted can exploit it immediately with no special privileges, no capital requirements beyond the swap itself, and no time constraints.

## Recommendation

The extension must check the end user's identity, not the intermediary's. The preferred fix is to have `MetricOmmSimpleRouter` encode `msg.sender` (the originating user) into `extensionData` for every `pool.swap()` call, and have `SwapAllowlistExtension.beforeSwap()` decode and check that address when `sender` is a known router. Alternatively, redesign the hook signature so the pool passes the originating user separately from the immediate caller, or require users to call `pool.swap()` directly for allowlisted pools (disabling router support for such pools).

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin allowlists Alice: `setAllowedToSwap(pool, alice, true)`.
3. Admin allowlists the router so Alice can trade via the router: `setAllowedToSwap(pool, router, true)`.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` — `msg.sender` inside `pool.swap()` is `address(router)`.
6. The pool calls `_beforeSwap(address(router), ...)` → extension receives `sender = address(router)`.
7. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully trades on a pool he was explicitly excluded from.

A Foundry integration test can confirm this by: deploying the extension and pool, calling `setAllowedToSwap` for Alice and the router, then calling `exactInputSingle` from Bob's address and asserting the swap succeeds rather than reverting with `NotAllowedToSwap`. [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
