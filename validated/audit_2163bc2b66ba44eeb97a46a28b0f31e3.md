### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the actual swapper, enabling full allowlist bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router contract address**, not the end user. If the pool admin allowlists the router (the only way to let any allowlisted user trade through the router), every non-allowlisted user can bypass the curated-pool restriction by routing through the same router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← router address when called via MetricOmmSimpleRouter
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap()` forwards this value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol:162-165
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap()` then checks `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct), and `sender` is whoever called `pool.swap()`. When the path is `user → MetricOmmSimpleRouter.exactInputSingle() → pool.swap()`, `sender` is the **router**, not the user.

This creates an irreconcilable conflict for any curated pool:

| Router allowlisted? | Effect |
|---|---|
| No | Allowlisted users cannot trade through the router at all (broken functionality) |
| Yes | Every non-allowlisted user can bypass the allowlist by calling the router |

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` with no mechanism to forward the original `msg.sender`:

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

The same issue applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

### Impact Explanation

A curated pool deployer uses `SwapAllowlistExtension` to restrict trading to a known set of addresses (e.g., KYC-verified counterparties, protocol-internal actors, or whitelisted market makers). Once the pool admin allowlists the router to enable router-based trading for their approved users, every non-allowlisted address can call `MetricOmmSimpleRouter.exactInputSingle()` and trade against the pool's liquidity without restriction. The allowlist guard is completely neutralized. LP funds are exposed to the full public swap flow that the pool admin explicitly intended to gate.

### Likelihood Explanation

The trigger requires only that the pool admin allowlists the router — a natural and expected operational step for any curated pool that wants to support the standard periphery. No privileged escalation, no malicious setup, and no non-standard tokens are required. Any unprivileged address can then call the public router functions.

### Recommendation

The `sender` forwarded to `beforeSwap` must represent the economic actor, not the intermediary. Two complementary fixes:

1. **In `MetricOmmSimpleRouter`**: store the original `msg.sender` in transient storage (alongside the existing callback context) and expose it via a standard interface so extensions can read the true initiator.
2. **In `SwapAllowlistExtension.beforeSwap`**: read the true initiator from the router's transient context when `sender` is a known router, or require pools to pass the end-user address explicitly through `extensionData`.

Alternatively, document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and enforce this at the factory level by reverting pool creation that combines both.

### Proof of Concept

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` configured as `beforeSwap` hook.
2. Pool admin allowlists Alice: `swapExtension.setAllowedToSwap(pool, alice, true)`.
3. Pool admin allowlists the router so Alice can use it: `swapExtension.setAllowedToSwap(pool, router, true)`.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, ...)` — `msg.sender` inside the pool is `router`.
6. `_beforeSwap(sender=router, ...)` is dispatched; `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true` → no revert.
7. Bob's swap executes against the curated pool's liquidity, bypassing the allowlist entirely.

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
