### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` from the pool's perspective. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router**, not the user. If the pool admin allowlists the router (necessary to enable router-based swaps), the allowlist is completely bypassed: any unprivileged user can swap on a curated pool by going through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← router address when called from MetricOmmSimpleRouter
    recipient,
    zeroForOne,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, ...)   // sender = router
    )
);
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

The pool sees `msg.sender = router`. The extension therefore checks whether the **router** is allowlisted, not the actual user. A pool admin who allowlists the router address (to enable router-based swaps for their curated users) inadvertently opens the pool to **all** users.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the position owner, the economically relevant actor), not `sender` (the adder contract). The swap allowlist has no equivalent correction.

---

### Impact Explanation

Any user can bypass the swap allowlist on a curated pool by routing through `MetricOmmSimpleRouter`. The pool admin's intent — restricting swaps to a specific set of addresses — is nullified. LP funds are exposed to unauthorized traders, including potential toxic flow that the allowlist was designed to exclude. This is a direct loss-of-protection impact on LP principal.

---

### Likelihood Explanation

Any pool that:
1. Deploys with `SwapAllowlistExtension` to restrict swaps, **and**
2. Allowlists the router address to support router-based swaps for their curated users

is immediately vulnerable. This is the natural configuration: a pool admin who wants to support the official periphery router for their allowlisted users must allowlist the router, which opens the gate to everyone. The trigger requires no special privileges — any user can call `MetricOmmSimpleRouter.exactInputSingle`.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should check the **original user**, not the intermediary. Two approaches:

1. **Pass the payer/originator through `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData`, and the extension decodes and checks it. This requires the extension to trust the router's encoding.

2. **Check `recipient` or require direct pool calls for allowlisted pools**: Document that allowlisted pools must not allowlist the router; users must call the pool directly. This is operationally fragile.

3. **Preferred — mirror `DepositAllowlistExtension`**: The extension should receive and check the true originator. The pool or router must forward the original `msg.sender` in a tamper-proof way (e.g., via transient storage set by the router before calling the pool, readable by the extension).

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Pool admin also allowlists the router to support router-based swaps.
swapExtension.setAllowedToSwap(address(pool), address(router), true);
swapExtension.setAllowedToSwap(address(pool), allowedUser, true);

// Attack: unauthorized user bypasses allowlist via router.
vm.prank(unauthorizedUser);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        recipient: unauthorizedUser,
        amountIn: 1000,
        amountOutMinimum: 0,
        zeroForOne: true,
        priceLimitX64: 0,
        deadline: block.timestamp + 1,
        extensionData: ""
    })
);
// Swap succeeds — allowlist bypassed.
// allowedSwapper[pool][router] == true, so the check passes for any caller of the router.
```

**Corrupted value**: `allowedSwapper[pool][router] = true` causes the extension to pass for every user who routes through the router, regardless of whether they are individually allowlisted. The pool admin's per-user access control is reduced to a per-router check, providing no meaningful restriction. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
