### Title
`SwapAllowlistExtension` Swap Guard Bypassed via Router — Any User Can Swap Through an Allowlisted Router on a Permissioned Pool - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates the swap, `sender` is the router's address, not the end-user's address. If the pool admin adds the router to the allowlist (the natural step to enable router-mediated swaps), every user — including non-allowlisted ones — can bypass the per-user gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← immediate caller, not the end-user
    recipient,
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
        (sender, recipient, ...)   // sender = pool's msg.sender
    )
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) calls the pool, the router is `msg.sender` of `pool.swap()`, so `sender = router`:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

The allowlist check therefore resolves to `allowedSwapper[pool][router]`. The pool admin has two bad choices:

| Admin action | Effect |
|---|---|
| Do **not** add router | Allowlisted users cannot use the router at all |
| Add router to allowlist | **Every** user — including non-allowlisted ones — can bypass the gate |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a permissioned pool: only specific addresses may swap. Once the pool admin adds the router to the allowlist (the expected operational step for any pool that wants to support the standard periphery), the allowlist is completely defeated. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against the pool at oracle-derived prices, bypassing the intended access control. This constitutes a broken core pool functionality causing unauthorized fund flows — the pool receives input tokens and pays out output tokens to actors the admin explicitly excluded.

---

### Likelihood Explanation

The trigger requires only that the pool admin adds the router to the allowlist, which is the natural and expected operational step for any pool that wants to support the standard periphery router. No privileged attacker capability is needed beyond calling the public router. The router is a deployed, immutable, publicly accessible contract. Any user who observes the allowlist configuration can immediately exploit it.

---

### Recommendation

The extension must gate the **end-user**, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Pass the original user through the router.** Add a `swapper` field to the router's `extensionData` payload and have the extension decode it when `sender` is a known router. This is fragile and requires trust in the router.

2. **Preferred: check `sender` only when it is not a router; otherwise decode the real user from `extensionData`.** The extension can maintain a registry of trusted routers and require them to attest the real user in `extensionData`.

3. **Simplest correct fix:** Remove router-level allowlisting entirely. Require end-users to call `pool.swap()` directly when a swap allowlist is active, and document this constraint clearly. The router's NatDoc already notes that `simulateSwapAndRevert` omits allowlist checks, indicating the design assumes direct pool calls for permissioned flows.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router added to enable periphery

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) → msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes for bob despite bob not being allowlisted

Result:
  - bob receives output tokens from the permissioned pool
  - The per-user allowlist is completely bypassed
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
