### Title
`SwapAllowlistExtension` checks the router address as `sender`, not the actual end-user — allowlist is fully bypassed when the router is allowlisted - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the actual end-user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. A pool admin who allowlists the router to enable router-mediated swaps for permitted users inadvertently opens the allowlist to every user on the network.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol L162-176
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is the router. The lookup is `allowedSwapper[pool][router]`.

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

The actual user's address is stored only in transient storage (`msg.sender` of the router call) and is never surfaced to the pool or the extension. The extension has no mechanism to recover it.

This creates two broken states:

| Router allowlisted? | Allowed user via router | Disallowed user via router |
|---|---|---|
| No | **Blocked** (broken UX) | Blocked |
| Yes | Passes | **Passes** (bypass) |

The bypass path is the critical one: a pool admin who allowlists the router so that permitted users can use the standard periphery entry point simultaneously grants every unpermitted user the ability to swap on the curated pool.

Note that `DepositAllowlistExtension` does **not** share this flaw — it checks `owner` (the position owner explicitly passed by the adder), not `sender` (the adder contract address).

### Impact Explanation

A curated pool configured with `SwapAllowlistExtension` to restrict trading to a specific set of counterparties loses its access-control guarantee the moment the pool admin allowlists the router. Any address on the network can then call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and trade on the pool as if they were allowlisted. This is a direct admin-boundary break: the pool admin's configured policy is bypassed by an unprivileged path through a supported production contract.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entry point. Pool admins who deploy curated pools and want to support standard tooling (wallets, aggregators, front-ends) will allowlist the router. The bypass is then immediately reachable by any address with no special privileges, no flash loan, and no multi-transaction setup.

### Recommendation

The extension must gate on the **economic actor**, not the intermediary. Two viable approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.

2. **Check `sender` only when it is not a known router; otherwise decode the real user from `extensionData`**: The extension is made router-aware.

3. **Structural fix**: Add an `originalCaller` field to the pool's `swap` signature (or a dedicated transient slot) so the pool can surface the true initiator to extensions without relying on `extensionData` conventions.

The `DepositAllowlistExtension` pattern (checking `owner`, which the adder sets to the actual position owner) is the correct model and should be replicated for the swap path.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for allowed users.
3. `alice` (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle` with the curated pool.
4. The pool calls `_beforeSwap(router, ...)`.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true`.
6. The swap executes successfully for `alice` despite her not being allowlisted.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
