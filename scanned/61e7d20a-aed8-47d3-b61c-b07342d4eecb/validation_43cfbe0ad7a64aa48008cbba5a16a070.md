### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address instead of the actual swapper, enabling allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks the router's address against the allowlist instead of the actual end-user. This creates a direct allowlist bypass: if the pool admin allowlists the router (the natural step to let allowlisted users use the router), every unprivileged user can bypass the curated-pool gate by routing through the router.

---

### Finding Description

**Pool → Extension argument binding**

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← pool's msg.sender, not the end-user
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged as the first positional argument to every configured extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...))
);
```

**The allowlist check**

`SwapAllowlistExtension.beforeSwap` then checks that `sender` (the immediate caller of `pool.swap`) is on the allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(recipient, ...)`, so `sender` received by the extension is the **router's address**, not the user's address.

**Contrast with `DepositAllowlistExtension`**

`DepositAllowlistExtension.beforeAddLiquidity` correctly gates by `owner` (the LP position owner, an explicit parameter separate from the caller):

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The swap path has no equivalent "actual user" parameter — only `sender` (the immediate caller). The swap allowlist therefore cannot correctly identify the end-user when the router is in the call stack.

**Two failure modes**

| Router allowlisted? | Result |
|---|---|
| Yes (`allowedSwapper[pool][router] = true`) | **Any user bypasses the allowlist** by routing through the router |
| No | **Allowlisted users cannot use the router** — core swap flow broken for them |

The bypass path is fully unprivileged: any user calls `MetricOmmSimpleRouter.exactInputSingle` with the curated pool address.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router (the natural step to let allowlisted users use the supported periphery) inadvertently opens the pool to all users. Any non-allowlisted address can call `exactInputSingle` or `exactInput` on the router and trade on the curated pool, bypassing the intended access control. This is a direct admin-boundary break via an unprivileged public path. If the curated pool is designed for specific market makers with tight spreads, unauthorized trading causes adverse selection losses for LPs.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap interface. Pool admins who want allowlisted users to be able to use the router will allowlist the router — this is the expected operational step. The bypass is therefore triggered by a routine admin action and is reachable by any public user with no special privileges.

---

### Recommendation

The `beforeSwap` hook signature should carry the original end-user identity separately from the immediate caller. Two options:

1. **Extend the hook signature**: Add an `originator` field (set by the pool from a trusted source such as `extensionData` or a dedicated router field) so the extension can check the actual user.
2. **Router-level identity forwarding**: Require the router to encode the actual user in `extensionData`; the extension decodes and checks it. The pool admin must configure the extension to trust the router's forwarded identity.

Until fixed, pool admins should not allowlist the router on pools using `SwapAllowlistExtension`, accepting that allowlisted users must call `pool.swap` directly.

---

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured on beforeSwap.
2. Pool admin calls setAllowedToSwap(pool, alice, true)       // allowlist alice
3. Pool admin calls setAllowedToSwap(pool, router, true)      // allowlist router so alice can use it
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
   → router calls pool.swap(recipient, ...)
   → pool calls _beforeSwap(msg.sender=router, ...)
   → extension checks allowedSwapper[pool][router] == true  ✓
   → swap executes — Bob bypassed the allowlist
5. Direct call by Bob:
       pool.swap(...)
   → extension checks allowedSwapper[pool][bob] == false  ✗ reverts
   → allowlist only blocks the direct path, not the router path
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
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
