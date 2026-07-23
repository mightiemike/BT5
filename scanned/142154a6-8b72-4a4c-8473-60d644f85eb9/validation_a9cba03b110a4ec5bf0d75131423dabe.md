### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (required for any allowlisted user to use the router), every unprivileged user can bypass the per-user allowlist by calling the router instead of the pool directly.

---

### Finding Description

**Invariant broken:** A curated pool's swap allowlist must gate the same actor that the economic action is attributed to, regardless of which supported public entrypoint reaches the pool.

**Root cause — wrong-actor binding in `SwapAllowlistExtension.beforeSwap`:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap()`: [4](#0-3) 

So the extension receives `sender = address(router)`, not the end user's address. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

**The dilemma this creates for the pool admin:**

- If the admin does **not** allowlist the router, no allowlisted user can ever use the router — all router-mediated swaps revert.
- If the admin **does** allowlist the router (the only practical choice), every non-allowlisted user can bypass the gate by calling `router.exactInputSingle()` instead of `pool.swap()` directly.

The pool admin has no way to simultaneously permit allowlisted users to use the router and block non-allowlisted users from doing the same, because the extension cannot distinguish between router calls originating from different end users.

---

### Impact Explanation

**Direct loss of curation / policy bypass on curated pools.** A pool configured with `SwapAllowlistExtension` to restrict trading to KYC'd, whitelisted, or otherwise vetted addresses is fully open to any unprivileged user who routes through `MetricOmmSimpleRouter`. The attacker can:

1. Trade on a pool they are not authorized to access.
2. Drain LP value through arbitrage or directional trading that the allowlist was designed to prevent.
3. Cause LP losses on pools whose liquidity was provisioned under the assumption that only vetted counterparties would trade.

This matches the Metric OMM allowed impact gate: **broken core pool functionality causing loss of funds** and **admin-boundary break where an unprivileged path bypasses a factory/pool role check**.

---

### Likelihood Explanation

- **Trigger is unprivileged:** any externally-owned account can call `MetricOmmSimpleRouter.exactInputSingle`.
- **No special setup required:** the attacker only needs to know the pool address and the router address.
- **Precondition is realistic:** the pool admin must have allowlisted the router, which is the only way to let legitimate users use the router at all.
- **No flash loan or complex state manipulation needed:** a single transaction suffices.

---

### Recommendation

The extension must gate the **end user**, not the intermediary. Two complementary fixes:

1. **In `SwapAllowlistExtension.beforeSwap`:** check the `sender` argument only when `msg.sender` (the pool's direct caller) is not a known periphery contract; otherwise require the periphery to forward the real user identity via `extensionData`.

2. **Preferred — pass the real user through `extensionData`:** require the router to encode the originating `msg.sender` into `extensionData` for allowlist-gated pools, and have the extension decode and check that value instead of (or in addition to) the `sender` argument.

3. **Alternatively:** add an `onlyDirect` mode to the extension that rejects any call where `sender` is a known router/adder contract, forcing allowlisted users to call the pool directly.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is the legitimate user
  - Pool admin calls setAllowedToSwap(pool, router, true)      // required so alice can use the router
  - Pool admin does NOT call setAllowedToSwap(pool, bob, true) // bob is the attacker

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — pool's msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes successfully for bob despite bob not being on the allowlist.

Result:
  bob trades on a curated pool he was never authorized to access.
  alice's LP position is exposed to an unauthorized counterparty.
``` [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L228-241)
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
