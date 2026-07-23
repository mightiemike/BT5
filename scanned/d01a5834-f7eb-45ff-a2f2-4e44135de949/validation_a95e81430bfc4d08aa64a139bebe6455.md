### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. If the pool admin allowlists the router address to enable router-mediated swaps for legitimate users, every unprivileged user can bypass the curated pool's swap allowlist by routing through the same public router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` encodes that value and calls each extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)   // sender = msg.sender of pool.swap()
)
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ..., params.extensionData
);
```

The router does **not** forward the original user's address into the pool's `swap()` call. The pool therefore sees `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist. Once `allowedSwapper[pool][router] = true`, the check passes for **every** caller who routes through the router, regardless of whether that caller is individually allowlisted.

### Impact Explanation

Any unprivileged user can execute swaps on a curated pool whose swap allowlist includes the router address. The pool receives input tokens from and sends output tokens to non-allowlisted counterparties, defeating the curation policy entirely. LP funds are exposed to counterparties the pool admin explicitly intended to exclude.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard periphery swap entrypoint. A pool admin who deploys a curated pool and wants allowlisted users to interact via the router must add the router to the allowlist — a natural and expected operational step. The admin has no way to achieve "router access for allowlisted users only" with the current design; the only available option is "router access for everyone." The bypass is therefore reachable whenever the pool admin takes the reasonable step of supporting router-mediated swaps.

### Recommendation

The extension must verify the actual end user, not the intermediary. Two approaches:

1. **Pass the original user through the router**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` in `extensionData` and have the extension decode and check it. This requires a protocol-level convention.

2. **Check `sender` against the allowlist only when `sender` is not a known router**: Maintain a registry of trusted routers in the extension; when `sender` is a trusted router, extract the real user from `extensionData`.

3. **Document the limitation clearly**: If the design intent is that `sender` is always the direct caller, document that curated pools must not allowlist shared routers and that allowlisted users must call the pool directly.

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
3. Pool admin calls setAllowedToSwap(pool, router, true)  // to let alice use the router
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(...) — msg.sender at pool = router
6. Extension checks allowedSwapper[pool][router] == true  → passes
7. Bob's swap executes on the curated pool despite not being allowlisted.
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at line 37, where `sender` is the router address when the swap is router-mediated, and the allowlist entry for the router grants access to all users indiscriminately. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
