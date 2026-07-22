### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `swap()`, so the extension checks the **router's address** against the allowlist, not the end-user's address. A pool admin who allowlists the router (the natural configuration for a pool that accepts router-mediated swaps) inadvertently opens the pool to every user who can call the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← whoever called swap(); the router when routed
    recipient,
    zeroForOne,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, ...)   // sender = router address
    )
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) calls `pool.swap()`, the router is `msg.sender`, so `sender` received by the extension is the **router address**. The end-user's address is never visible to the extension.

Consequence: if the pool admin allowlists the router address (the only way to permit router-mediated swaps on a curated pool), every user who can call the router bypasses the allowlist entirely. The extension's stated purpose — "Gates `swap` by swapper address, per pool" — is broken for the router path.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool: only specific addresses may trade. If the router is allowlisted (required for any router-mediated swap to succeed), any unpermissioned user can call `router.exactInputSingle()` and trade on the curated pool. This is a direct allowlist bypass enabling unauthorized swap execution, which constitutes broken core pool functionality and an admin-boundary break (the pool admin's curation policy is circumvented by an unprivileged path).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool admin who deploys a curated pool and also wants to support router users must allowlist the router, triggering the bypass. The attacker needs no special privilege — only the ability to call the public router.

---

### Recommendation

Pass the **original end-user** through the swap path so the extension can gate the economically relevant actor. One approach: add a `swapper` field to the swap call or extension data that the router populates with `msg.sender` before calling the pool. Alternatively, the extension should check `sender` only when `sender` is not a known router, and check the payer stored in transient callback context otherwise. The simplest correct fix is for the pool to expose the original initiator (e.g., via a `swapFor(address swapper, ...)` entry point on the router that threads the real user address into `extensionData`), and for `SwapAllowlistExtension` to decode and gate that address.

---

### Proof of Concept

```
1. Pool admin deploys MetricOmmPool with SwapAllowlistExtension as BEFORE_SWAP extension.
2. Pool admin calls:
       extension.setAllowedToSwap(pool, router, true);   // allowlist the router
   (This is required for any router-mediated swap to pass the guard.)
3. Disallowed user (not in allowedSwapper) calls:
       router.exactInputSingle({pool: pool, ...});
4. Router calls pool.swap() — msg.sender = router.
5. Pool calls _beforeSwap(sender=router, ...).
6. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
7. Disallowed user's swap executes on the curated pool.

Expected: revert NotAllowedToSwap().
Actual:   swap succeeds; allowlist is bypassed.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
