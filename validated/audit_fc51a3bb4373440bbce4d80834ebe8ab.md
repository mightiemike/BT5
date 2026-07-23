### Title
SwapAllowlistExtension Checks the Router's Address Instead of the Economic Actor, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. The pool always passes `msg.sender` of the `pool.swap()` call as `sender`. When `MetricOmmSimpleRouter` intermediates, `msg.sender` of `pool.swap()` is the **router**, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any pool admin who allowlists the router (the natural step to let their approved users trade via the router) simultaneously opens the pool to every unprivileged user who routes through the same contract.

---

### Finding Description

**`SwapAllowlistExtension.beforeSwap` — wrong actor binding**

The extension checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct key for the per-pool mapping). `sender` is the first argument forwarded by the pool. [1](#0-0) 

The pool's `swap()` function passes `msg.sender` of the `pool.swap()` call as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` encodes this value verbatim and forwards it to every configured extension: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Result**: the extension sees `sender = router` and evaluates `allowedSwapper[pool][router]`. If the router is allowlisted (the only way to let approved users trade via the router), every unprivileged user who calls the router bypasses the per-user restriction.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers, or protocol-internal actors) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The unauthorized user receives the pool's output tokens and pays the input tokens exactly as an approved user would. There is no slippage protection difference; the swap executes at the live oracle price. This is a direct loss of the curation policy and, depending on the pool's purpose, a direct loss of LP value if the pool was designed to trade only with specific counterparties.

---

### Likelihood Explanation

The bypass requires the router to be allowlisted on the pool. A pool admin who wants their approved users to be able to use the standard periphery router must allowlist the router — there is no other supported path. The moment the router is allowlisted, the allowlist is effectively open to all users. This is not a hypothetical misconfiguration; it is the only configuration that makes the extension compatible with the router. Any production pool that uses `SwapAllowlistExtension` and also supports router-based swaps is vulnerable.

---

### Recommendation

The extension must check the economic actor, not the direct caller of `pool.swap()`. Two approaches:

1. **Pass the original user through the router**: The router should forward the original `msg.sender` as a verified field in `extensionData`, and the extension should decode and verify it. This requires a trust model between the router and the extension.

2. **Check `sender` only when `sender` is not a known router**: The extension could maintain a registry of trusted routers and, when `sender` is a router, require the router to embed the real user in `extensionData` with a verifiable signature or transient-storage proof.

3. **Simplest safe fix**: Document that `SwapAllowlistExtension` is incompatible with the router for per-user restrictions, and provide a separate extension that reads the real user from a router-specific transient storage slot set before the pool call.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  allowedUser  = address allowlisted: allowedSwapper[pool][allowedUser] = true
  attackerUser = address NOT allowlisted

Step 1 (pool admin, legitimate):
  swapExtension.setAllowedToSwap(pool, router, true)
  // Admin allowlists the router so allowedUser can trade via the router.

Step 2 (attacker):
  router.exactInputSingle({
      pool:      pool,
      recipient: attackerUser,
      zeroForOne: true,
      amountIn:  X,
      ...
  })
  // Router calls pool.swap() with msg.sender = router.
  // Pool calls _beforeSwap(sender=router, ...).
  // Extension checks allowedSwapper[pool][router] → true → passes.
  // Swap executes. Attacker receives output tokens.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds; attacker bypasses the curated allowlist.
``` [6](#0-5) [7](#0-6) [8](#0-7)

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
