### Title
`SwapAllowlistExtension` Checks Router Address as Swapper Identity, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the router's address — not the actual end-user. If the router is allowlisted (a necessary step for any router-based swap to work), every user on-chain can bypass the per-user gate by routing through the router.

---

### Finding Description

**`SwapAllowlistExtension.beforeSwap()` identity check:** [1](#0-0) 

The check is `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool (correct key) and `sender` is the `sender` argument forwarded from the pool.

**`MetricOmmPool.swap()` sets `sender` = `msg.sender`:** [2](#0-1) 

`msg.sender` here is whoever called `pool.swap()`. When the user goes through the router, that is the router contract, not the user.

**`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly:** [3](#0-2) 

The router calls `pool.swap()` with no mechanism to forward the original `msg.sender` (the end-user) into the pool's `sender` argument. The pool receives `msg.sender = router`.

**`ExtensionCalling._beforeSwap()` forwards `sender` verbatim:** [4](#0-3) 

The router address propagates unchanged into `SwapAllowlistExtension.beforeSwap()` as `sender`.

**Result:** The allowlist check resolves to `allowedSwapper[pool][router]`. The pool admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | No user can swap through the router, even individually allowlisted ones |
| Allowlist the router | **Every** user on-chain can swap through the router, bypassing the per-user gate |

There is no configuration that allows "only allowlisted users may use the router."

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC-verified counterparties, institutional participants). Once the pool admin allowlists the router to enable router-based swaps for their users, any non-allowlisted address can call `router.exactInputSingle()` and execute swaps against the restricted pool. The allowlist is completely defeated. Unauthorized users can drain liquidity at oracle-quoted prices from a pool that was designed to be access-controlled.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` with `msg.sender = router`. [5](#0-4) 

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router — a routine operational step any admin would take when they want their allowlisted users to be able to use the standard periphery. The admin has no reason to suspect this opens the gate to all users; the extension's name and interface imply per-address gating. The bypass is then reachable by any unprivileged address with no special setup.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **economic actor** (the end-user), not the **call-chain intermediary** (the router). Two viable approaches:

1. **Pass end-user identity through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the router to be trusted to supply the correct identity.

2. **Check `recipient` instead of `sender`:** For single-hop swaps the recipient is often the end-user, but this breaks for multi-hop paths where intermediate recipients are the router itself.

3. **Dedicated router that forwards caller identity:** A new router variant that passes the original caller as a verified field the extension can trust, authenticated via a known router registry.

The simplest safe fix is option 1 combined with a registry of trusted routers: the extension only accepts the decoded identity when `sender` is a known trusted router; for direct calls it falls back to checking `sender` directly.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension (beforeSwap order)
  admin allowlists userA: allowedSwapper[pool][userA] = true
  admin allowlists router: allowedSwapper[pool][router] = true
    (necessary so userA can use the router)

Attack (userB, not allowlisted):
  userB calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, zeroForOne, amount, limit, "", "")
      msg.sender to pool = router
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
      checks: allowedSwapper[pool][router] == true  ✓
    → swap executes, userB receives output tokens

Result: userB bypassed the per-user allowlist and executed a swap
        in a pool intended to be restricted to userA only.
``` [1](#0-0) [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
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
