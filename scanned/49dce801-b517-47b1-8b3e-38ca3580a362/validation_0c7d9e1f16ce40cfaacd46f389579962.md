### Title
SwapAllowlistExtension gates the router address instead of the actual swapper, allowing any user to bypass the per-user swap allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which the pool sets to `msg.sender` of the `swap` call. When users swap through `MetricOmmSimpleRouter`, the router is `msg.sender`, so the extension checks the router's allowlist status rather than the actual user's. If the pool admin allowlists the router (a necessary step to enable any router-mediated swap for allowlisted users), every unprivileged user can bypass the per-user swap allowlist by routing through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument forwarded by the pool from its own `msg.sender`: [1](#0-0) 

The pool's `_beforeSwap` dispatcher passes `sender` (the pool's `msg.sender`) directly into the extension call: [2](#0-1) 

When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]` — the router's allowlist entry — rather than the individual user's entry.

The test suite confirms this binding: the allowlisted address is always `address(callers[0])` (the direct caller of the pool), not `users[0]` (the economic actor): [3](#0-2) 

This creates a structural inconsistency identical in class to the SpinLottery weight bug: the allowlist is *configured* per-user, but the *enforcement* operates on the intermediary (the router), not the intended identity.

### Impact Explanation

**High.** If the pool admin allowlists the router address (the only way to permit any router-mediated swap for allowlisted users), every user — including those explicitly excluded from the allowlist — can bypass the gate by calling `MetricOmmSimpleRouter.exactInputSingle`. The swap allowlist, intended to restrict pool access to specific counterparties, is rendered entirely ineffective for router-mediated flows. Pools relying on this extension for access control (e.g., private/institutional pools) are fully open to arbitrary swappers, directly impacting LP assets and protocol fee revenue.

Conversely, if the router is not allowlisted, allowlisted users cannot use the router at all, breaking the primary user-facing swap path.

### Likelihood Explanation

**Medium.** Any pool that deploys `SwapAllowlistExtension` and expects users to interact via the router (the standard periphery entry point) will encounter this. The pool admin allowlisting the router is the natural, expected configuration step. The bypass requires no special privileges — any user can call the public router.

### Recommendation

The `SwapAllowlistExtension` should gate on the economically relevant actor. Two options:

1. **Pass the original user through the router**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData`, and have the extension decode and check that address instead of `sender`.
2. **Check `recipient` instead of `sender`**: If the pool's swap interface guarantees `recipient` is the economic beneficiary, gate on `recipient`. However, this changes the semantics of the allowlist.

The `DepositAllowlistExtension` already demonstrates the correct pattern — it checks `owner` (the explicitly provided position owner), not `sender` (the operator/caller): [4](#0-3) 

The swap allowlist should adopt an equivalent explicit-identity pattern.

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER.
2. Pool admin allowlists user1 (the intended swapper):
       swapExtension.setAllowedToSwap(pool, user1, true)
3. Pool admin allowlists the router (required for any router-mediated swap):
       swapExtension.setAllowedToSwap(pool, router, true)
4. user2 (NOT allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle(pool, tokenIn, tokenOut, amountIn, ...)
5. Router calls pool.swap(user2_as_recipient, ...) — pool's msg.sender = router.
6. Pool calls _beforeSwap(router, user2, ...).
7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → no revert.
8. user2's swap settles successfully, bypassing the per-user allowlist entirely.
```

The bypass requires zero privileges beyond calling the public router. Any user excluded from the allowlist can drain liquidity or execute swaps the pool admin intended to block. [1](#0-0) [2](#0-1)

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
