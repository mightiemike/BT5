### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing per-user allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the immediate `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router (required for any router-based swap to succeed) inadvertently grants every user unrestricted swap access, completely defeating the per-user allowlist.

---

### Finding Description

**Hook check — wrong actor**

In `SwapAllowlistExtension.beforeSwap`, the guard is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the pool calls the extension). `sender` is the first argument forwarded by the pool. [1](#0-0) 

**How `sender` is bound**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, so `sender` = whoever called `pool.swap()`. [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that value verbatim into the extension call. [3](#0-2) 

**Router path — `sender` becomes the router**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The pool's `msg.sender` is therefore the router contract, not the end user. [4](#0-3) 

The same is true for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (all recursive hops). [5](#0-4) 

**Forced allowlist entry for the router**

For any router-based swap to succeed on an allowlisted pool, the admin must execute:

```solidity
extension.setAllowedToSwap(pool, address(router), true);
```

Once that entry exists, `allowedSwapper[pool][router]` is `true` for every call that arrives through the router — regardless of which end user initiated it. The per-user entries (`allowedSwapper[pool][user]`) are never consulted for router paths. [6](#0-5) 

---

### Impact Explanation

Any non-allowlisted address can bypass the swap allowlist on a curated pool by calling any `MetricOmmSimpleRouter` entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`). The hook sees `sender = router` and passes. The pool executes the swap at the oracle-anchored price. The intended per-user restriction — KYC gate, market-maker-only pool, regulatory boundary — is silently nullified for every user who routes through the supported periphery contract. This is an admin-boundary break: an unprivileged path bypasses a factory-configured protection that the pool admin believed was enforced.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entry point for EOAs. Any pool admin who deploys a `SwapAllowlistExtension` and also wants end users to be able to swap (rather than only allowing direct `pool.swap()` calls) must allowlist the router. The misconfiguration is the natural, expected setup — not an edge case. The admin has no in-protocol signal that allowlisting the router collapses the per-user gate.

---

### Recommendation

The extension must identify the true end user, not the immediate `pool.swap()` caller. Two viable approaches:

1. **Extension-data forwarding**: The router encodes the original `msg.sender` into `extensionData`; the hook decodes and checks that address. The pool's `_beforeSwap` already forwards `extensionData` unchanged.
2. **Separate sender field**: Add an authenticated `originalSender` field to the swap parameters that the pool populates from a transient-storage context set by the router before calling `pool.swap()`, analogous to how the liquidity adder stores its pay context.

Either approach ensures the allowlist gates the economically relevant actor regardless of which supported periphery path reaches the pool.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed user
3. Admin calls setAllowedToSwap(pool, router, true)  // required for router-based swaps

Attack
──────
4. bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})

5. Router calls pool.swap(bob_recipient, ...) — msg.sender = router.

6. Pool calls _beforeSwap(sender=router, ...).

7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → no revert.

8. Swap executes. bob receives output tokens.
   allowedSwapper[pool][bob] was never consulted.
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
