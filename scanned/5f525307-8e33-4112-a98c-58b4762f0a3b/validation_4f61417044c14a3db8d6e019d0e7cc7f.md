### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives from the pool. The pool always passes `msg.sender` of its own `swap()` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the actual user. The extension therefore checks whether the router is allowlisted, not whether the real swapper is allowlisted. Any pool admin who allowlists the router (the natural step to let their users trade through the standard periphery) simultaneously opens the allowlist to every address on-chain.

---

### Finding Description

**Call chain for a router-mediated swap:**

```
User
  → MetricOmmSimpleRouter.exactInputSingle()          [msg.sender = User]
      → MetricOmmPool.swap(recipient, ...)             [msg.sender = Router]
          → ExtensionCalling._beforeSwap(sender = msg.sender = Router, ...)
              → SwapAllowlistExtension.beforeSwap(sender = Router, ...)
                  checks: allowedSwapper[pool][Router]   ← wrong actor
```

In `MetricOmmPool.swap`, `_beforeSwap` is called with `msg.sender` as `sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router address when the user entered through the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the original caller's identity: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all four router entry points call `pool.swap` as `msg.sender = router`. [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` must choose one of two broken configurations:

1. **Allowlist the router** — the natural choice so that allowlisted users can trade through the standard periphery. This makes the allowlist meaningless: every address on-chain can bypass it by calling `MetricOmmSimpleRouter.exactInputSingle` with the curated pool, because the extension sees `sender = router` and the router is allowlisted.

2. **Do not allowlist the router** — allowlisted users are forced to call `MetricOmmPool.swap` directly and implement their own callback (`IMetricOmmSwapCallback`), which is not the intended UX and breaks the standard periphery integration.

In scenario 1, any non-allowlisted user can execute swaps on a pool that was designed to be restricted, receiving output tokens from the pool's LP reserves. This is a direct loss of the curation policy and constitutes a broken core pool functionality with fund-impacting consequences for LPs who deposited into a pool expecting restricted access.

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the primary user-facing swap interface documented and deployed for the protocol. Any pool admin who configures `SwapAllowlistExtension` and wants their allowlisted users to be able to trade through the standard router will allowlist the router address — the exact configuration that opens the bypass. The attacker needs no special privileges: a single call to `exactInputSingle` with the curated pool address is sufficient.

---

### Recommendation

The pool's `swap` function should accept an explicit `swapper` parameter (the economically relevant actor) that is separate from `msg.sender` (the technical caller). The router would pass `msg.sender` (the real user) as `swapper`, and the pool would forward it to extensions as `sender`. Alternatively, `SwapAllowlistExtension` could require the real user identity to be encoded in `extensionData` and verified against a signature or transient context set by the router — but the cleanest fix is at the pool interface level so that all extensions receive the correct actor without per-extension workarounds.

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Admin allowlists the router so that allowedUser can trade through it
extension.setAllowedToSwap(address(pool), address(router), true);
// allowedUser is NOT individually allowlisted

// Attacker (not allowlisted) bypasses the guard via the router:
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:           address(pool),
        tokenIn:        TOKEN0,
        recipient:      attacker,
        zeroForOne:     true,
        amountIn:       1e18,
        amountOutMinimum: 0,
        priceLimitX64:  0,
        deadline:       block.timestamp,
        extensionData:  ""
    })
);
// Swap succeeds: extension checked allowedSwapper[pool][router] == true
// Attacker receives TOKEN1 from the curated pool's LP reserves
// The allowlist policy is completely bypassed
```

The extension checks `allowedSwapper[pool][router]` (true) instead of `allowedSwapper[pool][attacker]` (false), so the guard passes and the non-allowlisted attacker receives output tokens from the restricted pool. [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
