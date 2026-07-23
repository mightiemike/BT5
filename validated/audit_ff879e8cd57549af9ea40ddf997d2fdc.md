### Title
SwapAllowlistExtension Gates Router Address Instead of End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which the pool sets to `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` equals the router contract address, not the end user. A pool admin who allowlists the router to enable router-based swaps inadvertently opens the allowlist to every unprivileged caller.

### Finding Description
`SwapAllowlistExtension` is a production extension designed to gate swap access on curated pools by checking the swapper's address. The `beforeSwap` hook receives `sender` as the first argument, which the pool sets to `msg.sender` of the `pool.swap()` call:

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to the extension dispatcher:

```solidity
_beforeSwap(
    msg.sender,   // sender = direct caller of pool.swap()
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

`ExtensionCalling._beforeSwap` forwards this verbatim to every configured extension. The `SwapAllowlistExtension` then checks:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
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

At this point `msg.sender` inside `pool.swap()` is the router, so `sender` passed to the extension is the router address. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

The `extensionData` bytes parameter in `beforeSwap` is silently ignored by the extension — there is no mechanism to thread the real end-user identity through the router into the guard.

This creates an inescapable dilemma for the pool admin:
- **Do not allowlist the router** → allowlisted users cannot use the router at all; they must call `pool.swap()` directly.
- **Allowlist the router** → every unprivileged user can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`.

There is no configuration that allows specific users to use the router while blocking others.

### Impact Explanation
Any unprivileged user can bypass the swap allowlist on a curated pool by routing through `MetricOmmSimpleRouter`. The allowlist invariant — "only approved addresses may swap" — is broken for the entire router-mediated path. LPs on curated pools that rely on the allowlist to control counterparty risk (e.g., KYC-gated pools, pools restricted to specific market makers) are exposed to swaps from arbitrary addresses, directly threatening LP principal through adverse selection or policy violation.

### Likelihood Explanation
The bypass requires the pool admin to have allowlisted the router. A pool admin who wants allowlisted users to be able to use the standard periphery router will naturally add the router to the allowlist, not realising this opens the gate to all callers. The router is the primary user-facing swap entrypoint documented in the periphery, making this a foreseeable and likely configuration. The attacker action is fully unprivileged: any address can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`.

### Recommendation
The `SwapAllowlistExtension` must check the economically relevant actor — the end user — not the intermediate contract. Two viable approaches:

1. **Extension-data identity forwarding**: Have the router encode the original `msg.sender` into `extensionData` and have the extension decode and verify it. The extension would then check `allowedSwapper[pool][decodedUser]` instead of `allowedSwapper[pool][sender]`.

2. **Recipient-based check**: Gate on `recipient` rather than `sender` when `sender` is a known router, or require the pool admin to configure a trusted-router registry so the extension can fall back to checking `recipient` for router-originated calls.

At minimum, the NatSpec and pool-configuration documentation must explicitly warn that allowlisting the router grants swap access to all callers, not just the intended subset.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, Alice, true)` — only Alice is intended to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — to let Alice use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` — `msg.sender` inside the pool is the router.
6. Pool calls `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Bob's swap executes against the curated pool, bypassing the per-user allowlist entirely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
