### Title
`SwapAllowlistExtension` checks the immediate pool caller (router) instead of the original user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of its own `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. If the router is allowlisted (the only way to enable router-based swaps for any user), every non-allowlisted user can bypass the curated pool's access control by routing through the public periphery.

---

### Finding Description

**`SwapAllowlistExtension.beforeSwap`** performs this check:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension's caller). `sender` is the first argument, which the pool sets to its own `msg.sender`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- becomes `sender` in the extension
    recipient,
    ...
);
```

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the **router contract**, not the original user:

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

So the extension evaluates `allowedSwapper[pool][router_address]` instead of `allowedSwapper[pool][original_user]`.

This creates an impossible configuration for the pool admin:

| Router allowlisted? | Effect |
|---|---|
| **No** | Allowlisted users cannot use the router — broken core functionality |
| **Yes** | Any user can bypass the allowlist by routing through the router |

The second row is the critical bypass. A pool admin who wants to support router-based swaps for their allowlisted users must add the router to the allowlist. Once the router is allowlisted, the allowlist provides no protection against any user who routes through the router.

The same issue applies to all router entry points: `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput` (recursive callback hops all call `pool.swap` from the router, so `sender = router` at every hop).

Note: `DepositAllowlistExtension` does **not** share this flaw — it checks `owner` (the position recipient), which is correctly preserved through the `MetricOmmPoolLiquidityAdder` call chain.

---

### Impact Explanation

Non-allowlisted users can swap on curated pools protected by `SwapAllowlistExtension` by routing through the public `MetricOmmSimpleRouter`. The allowlist — an admin-configured access control boundary — is bypassed by an unprivileged path. Depending on the pool's purpose (KYC gating, institutional-only pricing, RWA compliance), this constitutes a direct admin-boundary break with potential for unauthorized fund flows through the pool.

---

### Likelihood Explanation

Medium. The bypass requires the router to be allowlisted on the pool. This is a natural and expected configuration for any curated pool that intends to support router-based swaps for its allowlisted users. The router is a public, documented periphery contract, and the pool admin has no other mechanism to enable router access without opening the bypass.

---

### Recommendation

The extension must verify the **original initiating user**, not the immediate pool caller. Two viable approaches:

1. **Extension-data forwarding**: Require the router to encode the original `msg.sender` into `extensionData` for each hop, and have the extension decode and verify it. The pool admin would configure the router as a trusted forwarder.
2. **Separate sender/initiator fields**: Extend the `beforeSwap` hook signature to carry both the immediate caller and the original initiator, with the pool or router responsible for populating the initiator field.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` attached.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowlisted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted to enable router-based swaps.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
6. Pool calls `extension.beforeSwap(router, ...)` — extension checks `allowedSwapper[pool][router]` → `true` → no revert.
7. Bob's swap executes successfully on the curated pool despite not being allowlisted.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
