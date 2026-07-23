### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` equals the router contract address, not the actual end user. If the pool admin allowlists the router (which is required for allowlisted users to use the router at all), every user — including those not on the allowlist — can bypass the guard by routing through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct key) and `sender` is the first argument passed by the pool. In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- sender = whoever called pool.swap()
    recipient,
    ...
)
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)`:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
``` [3](#0-2) 

So `msg.sender` of the pool's `swap()` call is the **router address**, not the end user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an inescapable dilemma for the pool admin:

- **If the router is NOT allowlisted**: allowlisted users who route through the router are blocked — core swap functionality is broken for them.
- **If the router IS allowlisted**: every user on the network can bypass the allowlist by calling `router.exactInputSingle(...)`, because the extension sees the router (allowlisted) as the swapper.

The same issue applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap(...)` with `msg.sender` = router. [4](#0-3) 

### Impact Explanation

A pool admin who deploys a pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of counterparties (e.g., KYC'd addresses, institutional partners, or whitelisted bots). Once the router is allowlisted (the only way to let allowlisted users use the router), any unprivileged user can call `router.exactInputSingle` and execute swaps against the pool's LP reserves. LP funds are directly at risk from unauthorized traders who should have been blocked.

### Likelihood Explanation

The router is the primary user-facing entry point for swaps. Any pool that uses `SwapAllowlistExtension` and expects users to interact via the router must allowlist the router, at which point the guard is fully bypassed. The trigger requires only a standard public router call — no special privileges, no flash loans, no complex setup.

### Recommendation

The extension must check the **actual end user**, not the intermediary. Two approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. This requires a convention between router and extension.

2. **Check `sender` against a per-pool "trusted forwarder" registry**: The extension stores a mapping of `pool → trustedForwarder`. When `sender` is a trusted forwarder, the extension reads the actual user from `extensionData`. When `sender` is not a forwarder, it checks `sender` directly.

3. **Simplest fix**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory level (e.g., revert pool creation if both the router and `SwapAllowlistExtension` are configured without an explicit forwarder mapping).

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is the intended allowlisted user)
  - allowedSwapper[pool][router] = true  (required so alice can use the router)

Attack:
  1. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient=bob, ...)
  3. pool.swap: msg.sender = router → _beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] → true → no revert
  5. Bob's swap executes against LP reserves

Result:
  - Bob, who is not on the allowlist, successfully swaps on a restricted pool.
  - The allowlist guard is completely bypassed.
  - Direct pool call by bob (pool.swap directly) would correctly revert:
      allowedSwapper[pool][bob] = false → NotAllowedToSwap
``` [1](#0-0) [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
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
