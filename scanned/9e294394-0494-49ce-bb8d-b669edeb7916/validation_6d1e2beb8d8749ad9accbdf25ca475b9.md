Looking at the `SwapAllowlistExtension` and how `MetricOmmSimpleRouter` calls `pool.swap`, I can trace the exact wrong-actor binding.

**Key chain:**

1. `MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — the router is `msg.sender` of `pool.swap`.
2. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)` — so `sender = router address`.
3. `SwapAllowlistExtension.beforeSwap(address sender, ...)` checks `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][router]`.

The extension is supposed to gate individual users, but it sees the router address as `sender`, not the actual user. This creates an irresolvable contradiction identical in structure to the DYAD M-08 bug.

### Title
`SwapAllowlistExtension.beforeSwap` checks the router address as `sender` instead of the actual user, making per-user swap curation impossible and allowing all users to bypass the allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension` is designed to gate `swap` by individual swapper address. When `MetricOmmSimpleRouter` is the caller, the pool passes the router's address as `sender` to the extension. The extension checks `allowedSwapper[pool][router]`, not the actual user. The pool admin faces an irresolvable contradiction: allowlisting the router opens the gate to every user; not allowlisting it silently breaks the router for every allowlisted user.

### Finding Description

`SwapAllowlistExtension.beforeSwap` reads the first argument (`sender`) and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`sender` is whatever `MetricOmmPool.swap` passes as the first argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← the direct caller of pool.swap
    recipient,
    ...
);
```

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) executes, it calls `pool.swap` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

So `msg.sender` of `pool.swap` = the router contract. The extension receives `sender = router`, not the actual user. The allowlist is keyed by `allowedSwapper[pool][router]`.

This creates the same irresolvable contradiction as DYAD M-08:

| Scenario | Effect |
|---|---|
| Router **not** allowlisted | Every swap through the router reverts `NotAllowedToSwap`, even for individually allowlisted users — broken core functionality |
| Router **is** allowlisted | Every user, including non-allowlisted ones, can swap through the router — allowlist fully bypassed |

Neither state satisfies the invariant that the allowlist gates individual users. The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position owner), which is explicitly passed by the caller and preserved through the liquidity adder path — and the pool's own NatSpec confirms `owner` is the intended gate for deposits. No equivalent forwarding mechanism exists for the swap `sender`.

### Impact Explanation
A non-allowlisted user can trade on a curated pool by routing through `MetricOmmSimpleRouter` whenever the pool admin has allowlisted the router (the only way to let legitimate users use the router). This is a direct curation failure: the pool receives input tokens from and sends output tokens to parties the pool admin explicitly excluded. LP funds are exposed to trades from actors the pool was configured to reject.

### Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool that deploys `SwapAllowlistExtension` to restrict trading to a curated set of counterparties and also expects users to use the router (the normal path) will be affected. The pool admin has no correct configuration: they must choose between broken router access for allowlisted users or a fully open gate for everyone.

### Recommendation
The extension must check the actual end-user, not the intermediary. Two viable approaches:

1. **Pass user identity in `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it instead of `sender`. This requires a convention between the router and the extension.
2. **Check `sender` only when it is not a known router**: Maintain a registry of trusted routers in the extension; when `sender` is a trusted router, decode the real user from `extensionData`; otherwise check `sender` directly.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin allowlists Alice (`allowedSwapper[pool][alice] = true`) and the router (`allowedSwapper[pool][router] = true`) so Alice can use the router.
3. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`.
4. Router calls `pool.swap(bob, ...)` — `msg.sender` of `pool.swap` = router.
5. Pool calls `_beforeSwap(router, bob, ...)`.
6. Extension checks `allowedSwapper[pool][router]` → `true` → passes.
7. Bob's swap executes and settles. Bob receives output tokens despite never being allowlisted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L111-113)
```text
  /// @notice Swap allowlist rejected `msg.sender`.
  /// @dev Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set; `simulateSwapAndRevert` does not, so a passing simulation does not imply an allowed live swap.
  error NotAllowedToSwap();
```
