### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address instead of the actual swapper, enabling allowlist bypass or DoS via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the actual user. The allowlist therefore checks the router's permission, not the real swapper's, making the guard either permanently broken (DoS for allowlisted users) or trivially bypassable (if the router is allowlisted, all users pass).

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks the allowlist against that `sender`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`), the router calls `pool.swap(...)` directly: [4](#0-3) 

From the pool's perspective, `msg.sender` is the **router**, so `sender = router` reaches the extension. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` parameter (the actual position owner), not `sender`: [5](#0-4) 

This inconsistency confirms the mismatch in `SwapAllowlistExtension`.

---

### Impact Explanation

**Scenario A — Allowlist bypass (High):** A pool admin configures a swap allowlist to restrict trading to specific addresses, then adds the router to the allowlist so that those users can trade via the standard UI. Because the extension checks `allowedSwapper[pool][router]` and the router is allowlisted, **every user** — including those not on the allowlist — can bypass the restriction by routing through `MetricOmmSimpleRouter`. The access-control invariant is fully broken.

**Scenario B — Core functionality DoS (Medium):** A pool admin allowlists specific user addresses but does not add the router. Allowlisted users who attempt to swap through the router receive `NotAllowedToSwap`, even though they are individually permitted. The router — the primary user-facing entry point — is rendered unusable for all allowlisted users.

Both outcomes break the intended access-control boundary of the pool.

---

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and also expects users to interact via `MetricOmmSimpleRouter` (the standard periphery router) is affected. The router is the primary swap entry point documented in the periphery layer. A pool admin who wants to support both restricted access and router-based swaps will inevitably hit one of the two failure modes above without any malicious action required.

---

### Recommendation

The extension must check the **actual user**, not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`:** The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.

2. **Check `sender` only when it is not a trusted router; otherwise require the real user to be passed explicitly:** The extension maintains a registry of trusted routers and, when `sender` is a trusted router, falls back to a user address supplied in `extensionData`.

3. **Align with `DepositAllowlistExtension`:** If the intent is to gate by the position/trade owner rather than the immediate caller, the pool should pass the intended beneficiary (e.g., `recipient`) as the checked identity, and the extension should be updated accordingly.

---

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension as EXTENSION_1.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to allow router-based swaps for allowlisted users.
3. Non-allowlisted attacker calls:
     router.exactInputSingle({pool: pool, recipient: attacker, ...})
4. Pool.swap() fires with msg.sender = router.
5. _beforeSwap passes sender = router to the extension.
6. Extension checks allowedSwapper[pool][router] → true → no revert.
7. Attacker's swap executes despite never being on the allowlist.

Alternatively (DoS path):
1. Pool admin calls setAllowedToSwap(pool, alice, true).
2. Alice calls router.exactInputSingle({pool: pool, ...}).
3. Pool.swap() fires with msg.sender = router.
4. Extension checks allowedSwapper[pool][router] → false → NotAllowedToSwap.
5. Alice cannot use the router even though she is individually permitted.
```

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
