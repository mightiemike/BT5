### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router (a necessary step for legitimate users to perform multi-hop swaps), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol line 160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then gates on that `sender`:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

So `msg.sender` to the pool is the **router contract address**, not the user. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all of them call `pool.swap()` directly, making the router the `sender` the extension sees.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores the `sender` parameter (first arg, unnamed) and checks `owner` instead — the economically relevant actor for deposits. The swap extension has no equivalent correction.

---

### Impact Explanation

A pool admin who configures `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers) must also allowlist the router if any legitimate user needs multi-hop or single-hop routing. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for **every** caller of the router, including non-allowlisted users. The allowlist is completely bypassed for all router-mediated swaps.

This breaks the core invariant that only approved addresses may trade on a curated pool. Unauthorized users can execute swaps, receiving pool output tokens and paying input tokens, on pools that were explicitly configured to exclude them. This constitutes a direct policy breach and, in regulatory or compliance-sensitive deployments, a loss of the pool's intended access control guarantees — matching the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" impact category.

---

### Likelihood Explanation

The trigger is straightforward and requires no special privileges:

1. Pool is deployed with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin allowlists the router (address of `MetricOmmSimpleRouter`) so that legitimate users can perform single-hop or multi-hop swaps through the supported periphery path.
3. Any non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle()` (or any other router entry point) targeting that pool.
4. The pool receives `msg.sender = router`, the extension checks `allowedSwapper[pool][router] == true`, and the swap proceeds.

Step 2 is a natural and expected admin action — without it, no allowlisted user can use the router at all, breaking the supported periphery path. The admin faces a forced choice: either allowlist the router (opening the pool to everyone) or don't (breaking the router for legitimate users). There is no correct configuration under the current design.

---

### Recommendation

The extension must check the **original user**, not the intermediary. The pool already passes both `sender` (the direct caller) and `recipient` to the hook. The fix requires the router to forward the real user identity, or the extension to accept an authenticated user address from `extensionData`.

The cleanest fix mirrors how `DepositAllowlistExtension` handles the operator pattern: the pool's `addLiquidity` accepts an explicit `owner` separate from `msg.sender`. For swaps, the router should pass the real initiator in `extensionData`, and the extension should decode and verify it (with the pool as the trusted source of `msg.sender`). Alternatively, the pool interface could be extended to carry an explicit `swapper` field analogous to `owner` in `addLiquidity`.

A minimal stopgap: document that `SwapAllowlistExtension` is incompatible with any router or intermediary contract, and that pools using it must only be accessed via direct `pool.swap()` calls.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it
  - bob is NOT allowlisted

Attack:
  - bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) — msg.sender to pool = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  ✓
  - Swap executes for bob despite bob not being on the allowlist

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds — allowlist bypassed
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
