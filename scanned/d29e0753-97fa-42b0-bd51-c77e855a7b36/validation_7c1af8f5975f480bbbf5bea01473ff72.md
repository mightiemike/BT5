### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any disallowed user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap`. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap` is the **router**, not the original user. If the pool admin allowlists the router (the only way to support router-mediated swaps for their intended users), every unprivileged user can bypass the per-user allowlist by calling through the router.

### Finding Description

**Call chain for a router-mediated swap:**

```
User (disallowed) → MetricOmmSimpleRouter.exactInputSingle(pool=X, ...)
  → pool.swap(recipient, ...) [msg.sender = router]
    → _beforeSwap(sender = router, ...)
      → SwapAllowlistExtension.beforeSwap(sender = router, ...)
        → checks allowedSwapper[pool][router]  ← router address, NOT the user
```

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // = router when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this as the first argument to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool, `sender` = router. The check is `allowedSwapper[pool][router]`.

**The dilemma for the pool admin:**

| Admin action | Effect |
|---|---|
| Do NOT allowlist router | Intended users cannot swap through the router (broken UX) |
| Allowlist router | ALL users bypass the per-user allowlist (security failure) |

There is no configuration that simultaneously supports router-mediated swaps for intended users AND blocks disallowed users.

**Contrast with `DepositAllowlistExtension`:** The deposit extension correctly checks `owner` (the economic beneficiary, passed as a separate parameter), not `msg.sender` (the payer/intermediary). For swaps, no equivalent separate "originating user" parameter exists in the pool's `swap` signature — the pool only exposes `msg.sender` (the direct caller) and `recipient` (the output recipient).

### Impact Explanation

**High.** Any user disallowed by the pool admin can bypass the `SwapAllowlistExtension` by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) against a curated pool that has allowlisted the router. The pool's LP funds are exposed to swaps from unauthorized actors, breaking the core invariant of the allowlist extension. Curated pools (e.g., KYC-gated, institutional, or compliance-restricted pools) are rendered unenforceable.

### Likelihood Explanation

**Medium.** The pool admin must allowlist the router for the bypass to work. However, allowlisting the router is the natural and expected action for any pool admin who wants their permitted users to access the pool through the standard periphery interface. The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint, so this configuration is realistic in production.

### Recommendation

The `SwapAllowlistExtension` must gate the actual economic actor, not the intermediary. Two approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender`**: For swap allowlists, the `recipient` (output token receiver) is often the intended gated party. However, this is semantically different from gating the payer.

3. **Preferred — add an `originator` field to the swap hook signature**: The pool passes both `msg.sender` (the direct caller) and an explicit originator address, allowing extensions to check either. This is a core interface change.

The simplest safe fix without interface changes: document that `SwapAllowlistExtension` only works correctly for direct pool calls (not router-mediated), and provide a router-aware variant that decodes the originating user from `extensionData`.

### Proof of Concept

```solidity
// Setup:
// 1. Pool deployed with SwapAllowlistExtension
// 2. Admin allowlists alice (intended user) and the router (to support router swaps)
//    extension.setAllowedToSwap(pool, alice, true);
//    extension.setAllowedToSwap(pool, address(router), true);  // required for alice to use router
// 3. bob is NOT allowlisted

// Attack:
// bob calls the router directly — pool sees msg.sender = router
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 10_000e6,
    amountOutMinimum: 0,
    recipient: bob,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Extension checks allowedSwapper[pool][router] == true → swap succeeds
// bob, a disallowed user, has successfully swapped on a curated pool
```

**Relevant code locations:**

- `SwapAllowlistExtension.beforeSwap` checks `sender` (= router): [1](#0-0) 
- `MetricOmmPool.swap` passes `msg.sender` as `sender`: [2](#0-1) 
- `ExtensionCalling._beforeSwap` forwards `sender` to extension: [3](#0-2) 
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` as `msg.sender = router`: [4](#0-3) 
- `DepositAllowlistExtension` correctly checks `owner` (not `msg.sender`/payer) for contrast: [5](#0-4)

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
