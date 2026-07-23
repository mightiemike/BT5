### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` parameter, which is `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the original user. The allowlist therefore gates the router address rather than individual users, creating a complete bypass: any user can circumvent per-user restrictions by routing through the router if the router is allowlisted, or allowlisted users are silently blocked from using the router if it is not.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, ..., extensionData)   // msg.sender = router
     → ExtensionCalling._beforeSwap(msg.sender=router, recipient, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — the router, not the end user: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making itself the `msg.sender` of that call: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Exploitable scenario:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists specific KYC'd user addresses.
2. Pool admin also allowlists the router address so that legitimate users can use the router.
3. A non-allowlisted attacker calls `router.exactInputSingle()`.
4. The router calls `pool.swap()` with `msg.sender = router`.
5. The extension checks `allowedSwapper[pool][router]` → `true` → swap succeeds.
6. The attacker has bypassed the per-user allowlist entirely.

**Inescapable dilemma:** If the router is allowlisted, all users bypass the allowlist. If the router is not allowlisted, allowlisted users cannot use the router. There is no configuration that simultaneously allows legitimate router use and enforces per-user restrictions.

---

### Impact Explanation

The `SwapAllowlistExtension` is the primary mechanism for restricting pool access to specific users (e.g., KYC-gated, institutional, or permissioned pools). A complete bypass of this guard allows any unpermissioned user to execute swaps on a pool that is explicitly configured to deny them. This results in:

- Unauthorized token outflows from restricted pools.
- Violation of regulatory or contractual access controls.
- Potential loss of LP assets if the pool was designed to serve only trusted counterparties.

This matches the allowed impact gate: **broken core pool functionality causing loss of funds** and **admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path**.

---

### Likelihood Explanation

The bypass requires only that the pool admin allowlists the router (a natural and expected configuration for any pool that wants to support the standard periphery). The attacker needs no special privileges, no tokens beyond the swap input, and no setup beyond calling a public router function. Any user can trigger this on any allowlisted pool that also permits router access.

---

### Recommendation

The extension must check the **original user identity**, not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires router cooperation and is not enforceable by the extension alone.

2. **Check `recipient` instead of `sender`** (if the pool's design intent is to gate who receives output): Not semantically equivalent to gating the swapper.

3. **Preferred — gate at the pool level, not the extension**: The pool's `swap()` function should expose the original initiator (e.g., via a dedicated field or by requiring the router to forward the user identity in a verifiable way). The extension can then check that field.

The cleanest fix is for `MetricOmmSimpleRouter` to encode the original `msg.sender` into `extensionData` and for `SwapAllowlistExtension.beforeSwap()` to decode and verify it when `extensionData` is non-empty, falling back to `sender` for direct pool calls.

---

### Proof of Concept

```solidity
// Pool admin sets up allowlist: only alice is allowed
extension.setAllowedToSwap(pool, alice, true);
// Pool admin also allowlists the router so alice can use it
extension.setAllowedToSwap(pool, address(router), true);

// Attacker (not alice, not allowlisted) routes through the router
vm.prank(attacker);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: pool,
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: attacker,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// ✅ Swap succeeds — attacker bypassed the allowlist
// The extension checked allowedSwapper[pool][router] = true
// instead of allowedSwapper[pool][attacker] = false
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
