Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of end-user, allowing any caller to bypass per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`MetricOmmPool.swap` passes `msg.sender` (the immediate caller) as `sender` to `_beforeSwap`, which forwards it unchanged to `SwapAllowlistExtension.beforeSwap`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` inside `pool.swap` is the router contract, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any pool admin who allowlists the router to support standard tooling inadvertently grants every address unrestricted swap access, silently nullifying the per-user curation policy.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender`:** [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards `sender` unchanged:** [2](#0-1) 

**Step 3 — Extension checks `allowedSwapper[pool][sender]` where `sender` is the router:** [3](#0-2) 

**Step 4 — Router calls `pool.swap` directly, making itself `msg.sender` to the pool:** [4](#0-3) 

The check `allowedSwapper[msg.sender][sender]` resolves to `allowedSwapper[pool][router]`. There is no mechanism in the call chain to propagate the original end-user address. The `extensionData` field passes through unchanged but the extension does not decode it for an original-sender value.

## Impact Explanation

This is an admin-boundary break: the pool admin's per-user allowlist is bypassed by any unprivileged caller routing through `MetricOmmSimpleRouter`. Once the router is allowlisted (the natural operational step to enable router-mediated swaps), every address — including those explicitly excluded — can execute swaps against the pool. LP funds are exposed to counterparties the pool admin explicitly rejected, violating the pool's intended access control and potentially causing direct loss of LP assets if the curation policy exists to exclude adversarial or sanctioned counterparties.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin deploying a curated pool will routinely call `setAllowedToSwap(pool, router, true)` to support standard tooling. The `SwapAllowlistExtension` NatSpec says it "Gates `swap` by swapper address, per pool" with no caveat about router intermediation, making the misconfiguration a predictable operational mistake. The bypass is repeatable by any address with no special privileges.

## Recommendation

The extension must verify the economically relevant actor, not the intermediate contract. Two approaches:

- **Option A (extensionData convention):** The router encodes `msg.sender` into `extensionData`; `SwapAllowlistExtension.beforeSwap` decodes and checks it. Requires a documented convention between router and extension.
- **Option B (dedicated `originalSender` parameter):** Extend `IMetricOmmExtensions.beforeSwap` with an `address originalSender` field. The router passes `msg.sender` explicitly; the pool forwards it. The extension checks `originalSender` instead of `sender`.

Either fix must ensure the extension cannot be spoofed by a caller who fabricates the field.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin does **not** call `setAllowedToSwap(pool, alice, true)` (Alice is not individually allowlisted).
4. Alice calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The router calls `pool.swap(recipient, ...)` — `msg.sender` inside `pool.swap` is the router.
6. `_beforeSwap(router, ...)` is called; `SwapAllowlistExtension.beforeSwap` receives `sender = router`.
7. Check: `allowedSwapper[pool][router] == true` → passes.
8. Alice's swap executes successfully despite never being individually allowlisted, bypassing the curation policy entirely.

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
