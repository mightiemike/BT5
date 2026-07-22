### Title
`SwapAllowlistExtension` checks the router's address instead of the originating user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument passed by the pool, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. If the pool admin allowlists the router to support router-mediated swaps for legitimate users, every unpermissioned user can bypass the allowlist by calling the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks whether that `sender` is allowlisted for the calling pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

The pool's `msg.sender` is the router, so the extension receives `sender = router` and evaluates `allowedSwapper[pool][router]` — never the originating user's address. The router stores the original user only in its transient payment context (`_getPayer()`), which is never surfaced to the pool or the extension. [5](#0-4) 

The same substitution occurs in `exactInput` (intermediate hops use `address(this)` as payer), `exactOutputSingle`, and `exactOutput`.

---

### Impact Explanation

A pool admin who configures `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC-verified counterparties) faces an inescapable dilemma:

- **Do not allowlist the router** → every allowlisted user is also blocked from using the public router, breaking the supported periphery flow.
- **Allowlist the router** → every unpermissioned user can bypass the allowlist by calling `MetricOmmSimpleRouter.exactInputSingle()` (or any other router entry point), because the extension sees `sender = router` and passes the check.

In the second case, the allowlist provides zero protection: any address can trade on a pool that was intended to be curated. This constitutes a direct admin-boundary break — the pool admin's access-control policy is bypassed by an unprivileged path through a public, immutable periphery contract.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entry point documented and deployed alongside the protocol. Pool admins who want their allowlisted users to be able to swap conveniently will naturally allowlist the router. The bypass requires no special knowledge beyond knowing the router's address and calling a standard `exactInputSingle` with the restricted pool.

---

### Recommendation

The extension must gate on the economically relevant actor, not the direct pool caller. Two sound approaches:

1. **Pass the original user through the router**: Have the router encode the originating `msg.sender` in `extensionData` and have `SwapAllowlistExtension.beforeSwap()` decode and check that address when `sender` is a known router. This requires a trusted router registry or a signed attestation.

2. **Check `sender` only when it is not a known intermediary, and require the router to attest the real user**: The pool or extension can maintain a registry of trusted routers; when `sender` is a trusted router, the extension reads the real user from `extensionData` instead.

The simplest safe fix is to require that the router passes the originating user's address in `extensionData` and that `SwapAllowlistExtension` verifies it when `sender` is a registered router.

---

### Proof of Concept

```
Setup:
  pool  = MetricOmmPool with SwapAllowlistExtension (beforeSwap order = 1)
  admin sets allowedSwapper[pool][alice]   = true   // alice is KYC'd
  admin sets allowedSwapper[pool][router]  = true   // admin allowlists router so alice can use it
  bob is NOT allowlisted

Attack (bob):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({
       pool:      pool,
       recipient: bob,
       zeroForOne: true,
       amountIn:  X,
       ...
     })
  2. Router calls pool.swap(bob, true, X, ...) with msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  5. Swap executes; bob receives output tokens from the restricted pool.

Result: bob, a non-allowlisted user, successfully swaps on a curated pool
        whose allowlist was intended to restrict access to alice only.
``` [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
