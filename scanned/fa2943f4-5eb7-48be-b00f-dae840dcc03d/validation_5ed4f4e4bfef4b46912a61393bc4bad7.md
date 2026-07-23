Looking at the `SwapAllowlistExtension` and how `MetricOmmSimpleRouter` calls `pool.swap()`, I can trace a concrete wrong-actor binding issue.

**Key trace:**

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as `sender` to `_beforeSwap`. When the router calls `pool.swap()`, `msg.sender` is the router address.

In `SwapAllowlistExtension.beforeSwap()`, the check is:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```
where `msg.sender` = pool, `sender` = router. So the check is `allowedSwapper[pool][router]`.

If the admin allowlists the router (required for any legitimate user to use the router), the check passes for **all** users routing through it.

---

### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking `sender`, which is the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, `sender` is the router's address, not the actual end user. A pool admin who allowlists the router to enable legitimate users to swap through it inadvertently opens the allowlist to every user who routes through `MetricOmmSimpleRouter`.

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes `sender` (= `msg.sender` of `pool.swap()`) as the first argument to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle()` (or any other router entry point) calls `pool.swap()`, the router is `msg.sender` of that call: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The pool admin faces an inescapable dilemma:
- **Do not allowlist the router** → every individually-allowlisted user is blocked from using the router (broken core swap flow).
- **Allowlist the router** → `allowedSwapper[pool][router] = true` passes for every user who routes through it, completely nullifying the per-user allowlist.

The same wrong-actor binding applies to `exactInput` multi-hop paths (router calls each pool) and to `exactOutput` intermediate hops (where the previous pool becomes `msg.sender` of the next `pool.swap()` call inside `_exactOutputIterateCallback`): [5](#0-4) 

Note the contrast with `DepositAllowlistExtension`, which correctly checks `owner` (the position owner) rather than `sender` (the caller of `addLiquidity`), making it immune to the same router-intermediary problem: [6](#0-5) 

### Impact Explanation
Any user can bypass a curated pool's swap allowlist by routing through `MetricOmmSimpleRouter` once the admin has allowlisted the router. Unauthorized users gain full swap access to a pool whose LP providers deposited under the assumption that only vetted counterparties could trade. This constitutes a curation failure and a direct admin-boundary break via an unprivileged path.

### Likelihood Explanation
Medium. The trigger requires the pool admin to have allowlisted the router — a natural and expected operational step for any pool that wants to support the standard periphery swap path while maintaining an allowlist. Any pool that has both a `SwapAllowlistExtension` and a router allowlist entry is immediately vulnerable to any arbitrary user.

### Recommendation
The extension must identify the actual end user, not the intermediary. Two viable approaches:

1. **`extensionData` forwarding**: Have `MetricOmmSimpleRouter` encode the actual `msg.sender` (the end user) into `extensionData` for each hop, and have `SwapAllowlistExtension.beforeSwap()` decode and check that address when `extensionData` is non-empty.
2. **Recipient-based check**: For single-hop swaps where `recipient == actual user`, the extension could check `recipient` instead of `sender`. This does not generalize to multi-hop paths.

The cleanest fix is approach 1: the router appends the originating user address to `extensionData`, and the extension verifies it against the allowlist instead of (or in addition to) `sender`.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only `alice` is meant to swap.
3. Admin calls `setAllowedToSwap(pool, router, true)` — required so `alice` can use the router.
4. `bob` (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → no revert.
8. `bob`'s swap executes successfully in the curated pool, bypassing the per-user allowlist entirely.

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
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
