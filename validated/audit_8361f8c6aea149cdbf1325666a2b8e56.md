### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End User, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of the pool call is the **router**, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the actual trader is allowlisted. If the pool admin allowlists the router (the natural step to enable router-mediated swaps), every unprivileged user bypasses the allowlist entirely.

---

### Finding Description

**Pool → Extension argument binding**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)   // sender = router
)
```

**Extension check**

`SwapAllowlistExtension.beforeSwap` receives `sender = router` and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is the router. The lookup is `allowedSwapper[pool][router]`.

**Router entry point**

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

The pool sees `msg.sender = router`. The extension therefore never observes the actual end user.

**The mismatch**

| Scenario | Extension sees | Allowlist entry needed | Effect |
|---|---|---|---|
| User calls `pool.swap()` directly | `sender = user` | `allowedSwapper[pool][user]` | Correct gating |
| User calls via router | `sender = router` | `allowedSwapper[pool][router]` | Router-level gating only |

If the pool admin allowlists the router address so that allowlisted users can use the router, **every** user—including those the admin explicitly never allowlisted—can call through the router and pass the check. The allowlist is completely bypassed.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool: only specific addresses (e.g., KYC-verified traders, institutional counterparties) are permitted to trade. If the pool admin allowlists the router to enable router-mediated swaps for those users, any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) and trade against the pool without restriction. The allowlist guard fails open for the entire public router surface. Unauthorized traders can extract value from the pool at oracle-anchored prices, directly harming LP principal and defeating the curation policy.

---

### Likelihood Explanation

The trigger is fully unprivileged: any address can call the public router. The precondition—that the pool admin allowlists the router—is the natural operational step any admin would take when deploying a curated pool that is also expected to be accessible via the standard periphery. No special timing, no flash loan, no oracle manipulation is required. The bypass is deterministic and repeatable every block.

---

### Recommendation

The extension must check the **end user**, not the immediate pool caller. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a protocol-level convention.

2. **Check `sender` against a router registry and then verify the user**: The extension recognises known routers and, for those, reads the actual payer/user from a trusted transient-storage slot set by the router before calling the pool.

3. **Simplest fix**: Document that `SwapAllowlistExtension` is incompatible with the router and must only be used with direct pool calls, and add an `onlyPool` guard that also verifies the pool's `FACTORY` does not list the caller as a known router.

The analogous correct implementation is already present in `DepositAllowlistExtension`, which gates by `owner` (the economically relevant actor) rather than `sender` (the immediate caller), making it router-safe by design.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as a `beforeSwap` extension.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — allowlisting the `MetricOmmSimpleRouter` so that allowlisted users can use it.
3. Attacker (address never added to `allowedSwapper`) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
4. Pool calls `_beforeSwap(msg.sender=router, ...)`.
5. Extension evaluates `allowedSwapper[pool][router]` → `true` → passes.
6. Attacker's swap executes at oracle price, bypassing the allowlist entirely.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

Contrast with the correctly implemented deposit allowlist that gates by `owner` (the economically relevant actor), not `sender`: [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L228-240)
```text
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
