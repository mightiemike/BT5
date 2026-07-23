### Title
`SwapAllowlistExtension` gates the router address instead of the originating user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the originating user is allowlisted. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the gate to every user; a pool admin who does not allowlist the router blocks all router-mediated swaps, including those from legitimately allowlisted users.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap(); the router when routed
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol line 160-176
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (
  sender,       // ← still the router address
  ...
))
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is the router. The lookup is therefore `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

When `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant) calls `pool.swap()`:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
  );
```

The pool sees `msg.sender = router`. The extension sees `sender = router`. The original user's address is never visible to the extension.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to gate individual users faces a binary failure:

**Case A — router not allowlisted:** Every router-mediated swap reverts with `NotAllowedToSwap`, even for users the pool admin explicitly allowlisted. Allowlisted users are forced to call `pool.swap()` directly, which may not be possible for multi-hop paths or WETH-unwrap flows that require the router.

**Case B — router allowlisted (to enable router-mediated swaps for legitimate users):** The allowlist is completely bypassed. Any address can call `MetricOmmSimpleRouter.exactInputSingle` and swap on the curated pool, because the extension only checks `allowedSwapper[pool][router] == true`. The per-user curation is nullified.

In both cases the invariant "only allowlisted users may swap on this pool" is broken. Case B is the direct-loss path: disallowed users execute swaps on a pool that was designed to exclude them, draining LP value or violating the pool's economic design.

---

### Likelihood Explanation

The trigger is fully unprivileged. Any user who knows the pool uses `SwapAllowlistExtension` and that the router is allowlisted can call `MetricOmmSimpleRouter.exactInputSingle` with no special permissions. The router is a public, documented periphery contract. The pool admin's only recourse is to not allowlist the router at all, which breaks the UX for legitimate users.

---

### Recommendation

The extension must check the **originating user**, not the immediate caller of `pool.swap()`. Two approaches:

1. **Check `sender` only for direct calls; require the router to forward the original user.** The router would need to pass the original `msg.sender` as the `sender` argument to `pool.swap()`, but the pool's `swap()` signature fixes `sender = msg.sender`, so this requires a protocol-level change.

2. **Gate on `recipient` instead of `sender` for the router path.** This is fragile because `recipient` can be a third party.

3. **Preferred: the pool's `swap()` should accept an explicit `swapper` parameter** (separate from `msg.sender`) that the router fills with the originating user, and the extension should check that field. Until then, the `SwapAllowlistExtension` cannot safely coexist with the router on a curated pool.

As a short-term mitigation, the `SwapAllowlistExtension` documentation must warn that allowlisting the router opens the gate to all users, and curated pools should not allowlist the router.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists Alice directly: allowedSwapper[pool][alice] = true
  - Pool admin also allowlists the router so Alice can use it: allowedSwapper[pool][router] = true

Attack:
  - Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true → passes
  - Bob's swap executes on the curated pool

Result:
  - Bob bypassed the allowlist entirely
  - The pool admin's per-user curation is nullified
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
