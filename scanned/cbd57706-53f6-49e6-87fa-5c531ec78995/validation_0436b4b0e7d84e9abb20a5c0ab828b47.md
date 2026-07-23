### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the actual user. If the pool admin allowlists the router (the only way to let allowlisted users use the router), every user — including non-allowlisted ones — can bypass the gate by routing through the same public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever was passed — the router address when the user goes through the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no forwarding of the original caller's identity: [4](#0-3) 

This creates an irreconcilable dilemma for any pool admin who deploys a `SwapAllowlistExtension`:

- **If the router is NOT allowlisted**: allowlisted users cannot use the router at all (their address is not the `sender` the extension sees).
- **If the router IS allowlisted**: every user on the network can bypass the allowlist by routing through the same public router, because the extension only sees the router address.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` argument (the intended position owner), which `MetricOmmPoolLiquidityAdder` sets explicitly to the actual depositor: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict access to specific counterparties (e.g., KYC-verified traders, institutional market makers, or partners) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The allowlist — the only on-chain mechanism to gate swap access — provides no protection against router-mediated swaps once the router is allowlisted. Unauthorized flow into an oracle-priced pool causes adverse selection losses for LPs, as the pool was specifically designed to exclude uninformed or adversarial retail flow.

---

### Likelihood Explanation

The pool admin must allowlist the router for any allowlisted user to use the router. This is the natural and expected configuration: the admin allowlists both specific users (for direct calls) and the router (for UX). Once that configuration is in place, the bypass is trivially reachable by any user with no special privileges, no flash loan, and no front-running — a single call to `exactInputSingle` suffices.

---

### Recommendation

The extension must verify the original economic actor, not the immediate pool caller. Two approaches:

1. **Pass the original user through the router**: Modify `MetricOmmSimpleRouter` to encode the original `msg.sender` into `extensionData`, and modify `SwapAllowlistExtension.beforeSwap` to decode and check that address when `sender` is a known router.

2. **Check `recipient` instead of `sender`**: For single-hop swaps where the user is also the recipient, check `recipient` rather than `sender`. This is not general but avoids the router indirection for the common case.

The cleanest fix is approach 1: the router appends `abi.encode(msg.sender)` to `extensionData` before forwarding to the pool, and the extension reads it when the immediate `sender` is a recognized router.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls swapExtension.setAllowedToSwap(pool, router, true)   // allow router so users can use it
  admin calls swapExtension.setAllowedToSwap(pool, alice, true)    // alice is the only intended user

Attack:
  attacker (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: attacker, ...})

Trace:
  router.exactInputSingle
    → pool.swap(recipient=attacker, ...)   // msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, attacker receives tokens

Result:
  Attacker bypasses the allowlist. The extension never sees the attacker's address.
``` [6](#0-5) [3](#0-2) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
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
