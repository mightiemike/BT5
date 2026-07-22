### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the router is allowlisted (a natural admin action to enable router-based trading), every non-allowlisted user can bypass the curated pool's swap gate by calling through the router.

### Finding Description

The pool's `swap()` function passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that same `sender` into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` to the pool is the **router address**, so the extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Bypass path**: A pool admin who wants to enable router-based trading for allowlisted users allowlists the router address. Once the router is allowlisted, every user — including those explicitly excluded from the individual allowlist — can call `exactInputSingle` through the router and the extension passes, because it only sees the router address.

### Impact Explanation

A curated pool's entire swap-access policy is defeated. Any non-allowlisted user can execute swaps against the pool by routing through `MetricOmmSimpleRouter`. This is a direct loss of the curation invariant: unauthorized users trade against LP liquidity that was deployed under the assumption that only vetted counterparties could swap. LP funds are exposed to trades the pool admin explicitly intended to block, constituting a direct loss of LP assets and a broken core pool functionality.

### Likelihood Explanation

The trigger is unprivileged: any user can call the public router. The precondition — the router being allowlisted — is a natural and expected admin action for any curated pool that also wants to support the standard periphery UX. The router is a documented, supported entrypoint. No special setup or malicious initial configuration is required beyond the normal deployment of a curated pool.

### Recommendation

`SwapAllowlistExtension.beforeSwap` must gate on the **economically relevant actor**, not the intermediary. Two options:

1. **Check `sender` only when it is not a known router**: require the pool admin to register trusted routers, and when `sender` is a trusted router, check the `recipient` or require the router to forward the original user identity in `extensionData`.
2. **Require the router to forward the original caller**: add a field to `extensionData` that the router populates with `msg.sender` (the actual user), and have the extension verify that field when `sender` is a known router. The extension then checks the forwarded identity against the allowlist.

The simplest safe fix is to never allowlist the router as a blanket swapper, and instead document that router-mediated swaps on allowlisted pools are unsupported. However, the correct architectural fix is to thread the original user identity through the extension payload.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin allowlists router: setAllowedToSwap(pool, router, true)
  - Admin does NOT allowlist attacker: allowedSwapper[pool][attacker] = false

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender to pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
  5. Swap executes; attacker receives output tokens

Result:
  - attacker, who is not on the allowlist, successfully swaps against the curated pool
  - The allowlist invariant is broken; LP funds are exposed to unauthorized counterparties
``` [3](#0-2) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
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
