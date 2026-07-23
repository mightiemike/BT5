### Title
SwapAllowlistExtension gates the router address instead of the actual swapper, enabling full allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not the actual swapper. If the pool admin allowlists the router (the only way to let allowlisted users use the router), every non-allowlisted user can bypass the curation gate by routing through the router.

---

### Finding Description

**Actor binding in the pool → extension call chain**

`MetricOmmPool.swap` passes `msg.sender` (the immediate caller of the pool) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct key) and `sender` is whoever called `pool.swap()`. When the user goes through the router, `sender = address(router)`, not the user.

**Router call path**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same is true for `exactInput`, `exactOutputSingle`, and `exactOutput`. In every router-mediated path the pool sees `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]` — a single bit that covers every user who ever routes through that contract.

**The dilemma this creates for the pool admin**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all — broken UX |
| Allowlist the router | Every non-allowlisted user can bypass the gate by routing — broken security |

There is no configuration that simultaneously lets allowlisted users use the router and blocks non-allowlisted users.

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-gated, jurisdiction-restricted, or partner-only) with `SwapAllowlistExtension` and allowlists the router to support normal user flows inadvertently opens the gate to all users. The allowlist invariant — "only approved addresses may swap" — is completely broken for router-mediated swaps. Non-allowlisted users can trade in a pool designed to restrict access, violating the pool's curation policy and any downstream compliance or economic assumptions built on it.

---

### Likelihood Explanation

Allowlisting the router is the natural and expected configuration for any curated pool that wants to support the protocol's own periphery. The bypass requires no special knowledge, no privileged access, and no unusual token behavior — any user can call `exactInputSingle` on the public router. The only precondition is that the pool admin has made the reasonable configuration choice of allowlisting the router.

---

### Recommendation

The extension must check the **economically relevant actor** — the end user — not the intermediary contract. Two viable approaches:

1. **Router-forwarded identity**: The router encodes `msg.sender` (the real user) into `extensionData` and the extension decodes and checks it. This requires a coordinated change to both the router and the extension.
2. **Separate sender vs. swapper parameters**: The pool could expose a dedicated `swapper` field (distinct from `msg.sender`) that the router populates with the real user address, and the extension checks that field.

Until one of these is implemented, `SwapAllowlistExtension` should document that it cannot enforce per-user restrictions for router-mediated swaps, and curated pools should not allowlist the router.

---

### Proof of Concept

```
Setup:
  pool admin deploys pool with SwapAllowlistExtension
  pool admin: setAllowedToSwap(pool, alice, true)       // KYC'd user
  pool admin: setAllowedToSwap(pool, router, true)      // to let alice use the router

Attack:
  bob (non-KYC'd) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

Execution trace:
  router.exactInputSingle()          msg.sender = bob
    pool.swap(recipient=bob, ...)    msg.sender = router
      _beforeSwap(sender=router, ...)
        SwapAllowlistExtension.beforeSwap(sender=router, ...)
          allowedSwapper[pool][router] == true  ✓  (no revert)
      swap executes for bob
```

Bob's swap succeeds. The allowlist checked the router, not Bob. [3](#0-2) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
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
