### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the immediate `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. If the pool admin allowlists the router (a natural step to enable periphery-based swaps), every unpermissioned user can bypass the per-user gate by routing through the router.

### Finding Description
`ExtensionCalling._beforeSwap` forwards `msg.sender` of the pool's `swap` call as the `sender` argument to every registered extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol (swap path)
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap(recipient, ...)`. The pool's `msg.sender` is the router, so the extension receives `sender = router`. The check becomes:

```
allowedSwapper[pool][router]
```

not

```
allowedSwapper[pool][end_user]
```

A pool admin who wants to support router-based swaps for allowlisted users has only two options:
1. Allowlist the router address → every user on earth can swap through the router, defeating the per-user gate entirely.
2. Do not allowlist the router → every legitimate allowlisted user is also blocked from using the router.

There is no configuration that simultaneously permits router-based swaps for allowlisted users and blocks non-allowlisted users.

### Impact Explanation
Any user who is not on the allowlist can execute swaps on a curated pool by routing through `MetricOmmSimpleRouter` whenever the router address is in `allowedSwapper[pool]`. The pool admin's intent to restrict trading to specific counterparties is silently nullified. Depending on the pool's purpose (e.g., institutional-only liquidity, KYC-gated pools, rate-limited market-making), this allows unauthorized price-taking, front-running, or extraction of LP value from a pool that was never meant to be publicly accessible.

### Likelihood Explanation
The scenario is realistic and not contrived:
- Pool admins routinely allowlist the official periphery router so that their own allowlisted users can trade without calling the pool directly.
- The `SwapAllowlistExtension` interface gives no indication that allowlisting the router is semantically different from allowlisting an individual user; the admin has no on-chain signal that doing so opens the gate to everyone.
- The router is a single, well-known, immutable address, so allowlisting it is a one-time admin action that permanently exposes the bypass.

### Recommendation
Pass the true end-user identity through the hook. Two approaches:

1. **Encode the original caller in `extensionData`**: The router should append `msg.sender` (the end user) to `extensionData` before forwarding it to the pool. The extension then decodes and checks that address instead of `sender`.
2. **Check `sender` only for direct pool calls; decode user from `extensionData` for router calls**: The extension can distinguish the two paths by checking whether `sender` is a known router and, if so, requiring the real user identity in `extensionData`.

Either way, the allowlist must key on the economically relevant actor (the end user who controls the input tokens), not the intermediary contract.

### Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` in the `BEFORE_SWAP_ORDER`.
2. Admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps for allowlisted users.
3. Admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. Attacker calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Pool calls `_beforeSwap(router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router] == true` → no revert.
7. Attacker's swap executes on the curated pool despite never being allowlisted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L271-295)
```text
      uint256 balance1Before = balance1();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount1Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount1Delta > 0 && balance1Before + uint256(amount1Delta) > balance1()) {
        revert IncorrectDelta();
      }
    }

    uint256 packedSlot0Final = Slot0Library.loadPackedSlot0();
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
      extensionData
    );
```
