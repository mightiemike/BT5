### Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating User, Allowing Any Caller to Bypass a Curated Pool's Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the direct caller of `MetricOmmPool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the originating user. A pool admin who allowlists the router (the natural operational step to let their curated users trade through the supported periphery) simultaneously opens the pool to every user on the network, because any caller can reach the pool through the same public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap()
  recipient,
  zeroForOne,
  ...
);
```

`ExtensionCalling._beforeSwap` encodes that value and calls each extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
  )
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is on the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly, making the router the `msg.sender` of that call:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

The extension therefore receives `sender = address(router)` and evaluates `allowedSwapper[pool][router]`. The original user's address is never seen by the guard.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and wants their allowlisted users to be able to trade through the supported periphery must add the router to the allowlist (`setAllowedToSwap(pool, router, true)`). Once the router is allowlisted, the guard passes for every call that arrives through the router regardless of who the originating user is. Any address on the network can call `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool and the extension will approve the swap because `allowedSwapper[pool][router] == true`. The entire curation policy is nullified. Users who were explicitly blocked can trade freely; the pool admin has no mechanism to distinguish them at the extension layer.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap interface for end users. Any pool admin who configures a `SwapAllowlistExtension` and also wants their allowlisted users to use the router (a natural and expected operational requirement) will trigger this condition. The router is a public, permissionless contract, so no privileged access is required to exploit it. The attacker only needs to call a standard router function with the target pool address.

---

### Recommendation

Pass the originating user through the call chain so the extension can gate on the economically relevant actor. One approach: add an `originator` field to the swap callback data or to a dedicated transient slot in the router, and have the pool forward it as a separate argument to the extension. Alternatively, document that `sender` in the extension is always the direct pool caller and require pool admins to allowlist the router and rely on a separate per-user check inside the extension (e.g., via signed permits in `extensionData`). The simplest correct fix is to have the router store `msg.sender` in transient storage before calling the pool, and expose a `getSwapOriginator()` view that the extension can call back to retrieve the true user.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is the intended user
  - Pool admin calls setAllowedToSwap(pool, router, true)      // needed so alice can use the router

Attack:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({
        pool: curated_pool,
        tokenIn: token0,
        recipient: bob,
        amountIn: X,
        ...
    })
  - Router calls pool.swap(bob_recipient, ...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  - Swap executes; bob receives output tokens from the curated pool
  - alice's exclusive access policy is violated with zero privileged setup
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
