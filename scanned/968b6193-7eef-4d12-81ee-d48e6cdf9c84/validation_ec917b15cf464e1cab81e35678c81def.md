### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Non-Allowlisted Users to Bypass Swap Restrictions via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router to support router-mediated swaps, every user — including non-allowlisted ones — can bypass the per-user allowlist by routing through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the contract that calls the extension) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's own `swap()` call. [2](#0-1) 

`_beforeSwap` in `ExtensionCalling` encodes `sender` (the pool's `msg.sender`) into the call to the extension: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The router stores the original user as the payer in transient storage (for the payment callback), but the pool's `msg.sender` — and therefore the `sender` forwarded to the extension — is the **router address**, not the end user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable split:

| Pool admin intent | Router allowlisted? | Result |
|---|---|---|
| Allow specific users via router | Yes | **All users bypass allowlist** |
| Block non-allowlisted users from router | No | All router-mediated swaps blocked, even for allowlisted users |

There is no configuration that simultaneously allows allowlisted users to swap through the router while blocking non-allowlisted users.

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to known counterparties (e.g., KYC-verified market makers, whitelisted institutions, or to exclude known MEV bots) loses that protection the moment the router is allowlisted. Any unprivileged user can call `router.exactInputSingle` or `router.exactInput` and trade against the pool's liquidity. This breaks the core access-control invariant of the curated pool and exposes LP funds to unauthorized extraction by actors the pool admin explicitly intended to exclude.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. A pool admin who wants to support normal UX (slippage protection, multi-hop, deadline checks) must allowlist the router. The bypass is therefore reachable by any user on any curated pool that supports router-mediated swaps — a standard production configuration.

### Recommendation

The extension must check the **original end user**, not the intermediary router. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. The pool admin must configure the extension to trust this field only when `sender == router`.

2. **Check `sender` only when `sender` is not a trusted router**: The extension maintains a registry of trusted routers; when `sender` is a trusted router, it reads the actual user from `extensionData`; otherwise it checks `sender` directly.

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin allowlists Alice: extension.setAllowedToSwap(pool, alice, true)
3. Pool admin allowlists the router (to support router UX):
       extension.setAllowedToSwap(pool, router, true)
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
5. Router calls pool.swap(bob, ...) — pool's msg.sender = router.
6. Pool calls extension.beforeSwap(router, bob, ...) — sender = router.
7. Extension checks allowedSwapper[pool][router] → true → PASSES.
8. Bob's swap executes against the curated pool's liquidity.
   Bob was never allowlisted; the guard is fully bypassed.
``` [5](#0-4) [6](#0-5) [7](#0-6) [3](#0-2)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-170)
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
