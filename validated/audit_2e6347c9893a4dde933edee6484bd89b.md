### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any Caller to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router (necessary for any router-mediated swap to succeed for legitimate users), every unpermissioned user can bypass the allowlist by calling the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

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

`msg.sender` here is the pool (correct pool-key binding). `sender` is whatever the pool passes as the first argument to `_beforeSwap`. `ExtensionCalling._beforeSwap` forwards the `sender` parameter it receives from the pool's `swap` function:

```solidity
// ExtensionCalling.sol L149-177
function _beforeSwap(address sender, address recipient, ...) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
    );
}
```

The pool's `swap` function is called by the router with no explicit `sender` argument — the pool uses its own `msg.sender` (the router) as `sender`. When `MetricOmmSimpleRouter.exactInputSingle` executes:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

The pool's `msg.sender` is the router contract. The pool passes the router address as `sender` to `_beforeSwap`, so the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actual_user]`.

This creates an irresolvable dilemma for pool admins:

- **If the router is NOT allowlisted**: all router-mediated swaps revert, even for legitimately allowlisted users.
- **If the router IS allowlisted** (to enable router-mediated swaps for legitimate users): every user — including those explicitly excluded from the allowlist — can bypass the gate by calling the public router.

The `DepositAllowlistExtension` avoids this problem by checking `owner` (the second parameter, which is the LP position owner explicitly passed by the pool), not `sender`. The swap extension has no equivalent economically-bound identity to fall back on.

---

### Impact Explanation

Any user excluded from a pool's swap allowlist can execute swaps on that pool by routing through `MetricOmmSimpleRouter`. The allowlist provides zero protection against router-mediated swaps once the router is allowlisted. Curated pools (e.g., KYC-gated, institution-only, or compliance-restricted pools) lose their access control entirely on the router path, allowing unauthorized parties to trade against pool liquidity and extract value from LP positions.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless periphery contract. Any user can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` targeting any pool. The bypass requires no special privileges, no flash loans, and no multi-transaction setup — a single router call suffices. Pool admins who deploy a `SwapAllowlistExtension` and allowlist the router (a natural operational step) are immediately exposed.

---

### Recommendation

The pool must propagate the original caller's identity through to the extension. Two approaches:

1. **Transient-storage originator**: The router writes the actual `msg.sender` into a transient slot before calling `pool.swap`, and the pool reads it to pass as `sender` to `_beforeSwap`. This mirrors the `_inSwapPriceProvider` pattern already used in the codebase.

2. **Extension-data convention**: Define a standard encoding where the router prepends the actual user address to `extensionData`, and `SwapAllowlistExtension` decodes and checks that address instead of the raw `sender` parameter.

Either way, the extension must check the economically relevant actor — the wallet that initiated the router call — not the intermediate contract that called `swap`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it
  - bob is NOT on the allowlist.

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — pool's msg.sender = router
  3. Pool calls _beforeSwap(router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes successfully for bob despite bob not being allowlisted.

Result: bob bypasses the allowlist and swaps on a curated pool.
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
