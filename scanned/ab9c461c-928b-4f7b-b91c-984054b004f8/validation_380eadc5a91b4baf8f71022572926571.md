### Title
`SwapAllowlistExtension` Gates on Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][end-user]`. A pool admin who allowlists the router to enable standard periphery access inadvertently opens the pool to every user, completely defeating the allowlist.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router**, not the end-user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irresolvable dilemma for the pool admin:

| Admin action | Consequence |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the standard periphery at all — core swap flow is broken for them |
| **Allowlist the router** | Every user on the network can call the router and bypass the allowlist entirely |

The same identity mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, and to the recursive `_exactOutputIterateCallback` path where intermediate hops call `pool.swap` with `msg.sender = router`: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific institutional or whitelisted counterparties is fully bypassed the moment the admin allowlists the router to support standard periphery usage. Any unprivileged user can call `exactInputSingle` or `exactInput` on the router and execute swaps in the restricted pool. LPs who deposited under the assumption that only vetted counterparties would trade against them are exposed to the full public, enabling unauthorized value extraction and breaking the pool's intended access model. This is a direct broken-core-functionality / unauthorized-swap impact.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical periphery entry point documented and expected to be used by end-users. Any pool admin who deploys a swap-allowlisted pool and wants those allowlisted users to be able to use the router will naturally allowlist the router address, triggering the bypass. The trigger requires no special privilege — any public user with the router address can exploit it.

---

### Recommendation

The extension must gate on the **end-user identity**, not the immediate caller of `pool.swap()`. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that address. The pool admin configures the extension to trust the router as a forwarding agent.

2. **Check `sender` only for direct calls; decode user from `extensionData` for router calls**: The extension inspects whether `sender` is a known router and, if so, reads the real user from the extension payload.

Either approach preserves the allowlist invariant regardless of whether the user calls the pool directly or through the router.

---

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension in the BEFORE_SWAP_ORDER slot.
2. Admin calls setAllowedToSwap(pool, user1, true)   // allowlist user1
3. Admin calls setAllowedToSwap(pool, router, true)  // allowlist router so user1 can use periphery
4. user2 (NOT allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: pool,
           recipient: user2,
           zeroForOne: true,
           amountIn: X,
           ...
       })
5. Router calls pool.swap(user2, true, X, ...) → msg.sender inside pool = router
6. Pool calls _beforeSwap(router, user2, ...)
7. Extension evaluates: allowedSwapper[pool][router] == true  ✓
8. Swap executes for user2 — allowlist completely bypassed.
``` [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
