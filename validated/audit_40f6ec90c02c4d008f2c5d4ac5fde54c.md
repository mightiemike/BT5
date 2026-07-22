### Title
`SwapAllowlistExtension` Checks Router Address Instead of Original User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is `msg.sender` of the pool's `swap` call. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. A pool admin who allowlists the router to enable router-mediated swaps simultaneously opens the pool to every user, completely defeating the per-user allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool. The pool always passes its own `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` encodes this value verbatim as the first positional argument to every registered extension: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the **router contract address**, not the originating user:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // original user identity is never forwarded
);
``` [4](#0-3) 

The router stores the original user only in transient callback context for payment settlement; it is never passed to the pool's `swap` call and therefore never reaches the extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

A pool admin who wants allowlisted users to be able to use the router **must** add the router to the allowlist. The moment the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for every caller of every router entry-point, because the router is always the `sender` the extension observes. The per-user allowlist is completely inoperative for all router-mediated swaps.

---

### Impact Explanation

Any user can bypass a per-user swap allowlist on a curated pool by routing through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps at oracle prices against pool liquidity, directly draining LP assets. The allowlist guard — the sole access-control mechanism on such pools — fails open for the primary public entry-point. This is a direct loss of LP principal and a broken core pool invariant (curated pool policy is not enforced).

---

### Likelihood Explanation

Medium-High. Every pool that deploys `SwapAllowlistExtension` to restrict swaps to specific addresses **and** needs to support the standard router (the normal user-facing entry-point) is affected. The pool admin is forced into a binary choice: block all router-mediated swaps (by not allowlisting the router) or open the pool to all users (by allowlisting the router). There is no configuration that allows only specific users to swap through the router. The bypass requires only a standard `exactInputSingle` call — no special privileges or setup.

---

### Recommendation

The extension must check the economically relevant actor, not the intermediary. Options:

1. **Explicit original-sender forwarding:** Add an `originalSender` field to the pool's `swap` signature that the router sets to `msg.sender` before delegating to the pool, and have the extension check that field.
2. **Extension-data convention:** Define a signed or authenticated `extensionData` payload that the router encodes with the original user's address; the extension decodes and verifies it.
3. **Documentation guard:** If the design intent is that `SwapAllowlistExtension` only works for direct pool calls, document this explicitly and prevent the router from being added to any allowlist-gated pool.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only `alice` is permitted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary so `alice` can use the router.
4. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
6. Pool calls `_beforeSwap(router, ...)` → extension receives `sender = router`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `bob`'s swap executes at oracle price against pool liquidity; the allowlist is bypassed.

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
