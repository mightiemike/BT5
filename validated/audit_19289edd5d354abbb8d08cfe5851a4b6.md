### Title
SwapAllowlistExtension Bypassed via MetricOmmSimpleRouter: Any User Can Swap on Restricted Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument it receives from the pool. When `MetricOmmSimpleRouter` mediates a swap, the pool's `msg.sender` is the router contract, so the extension checks the router's address against the allowlist — not the actual end-user's address. If the router is allowlisted (the only way to permit router-mediated swaps), every user on the network can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry-point) calls `pool.swap(...)` directly, making the router the `msg.sender` inside the pool for every hop: [4](#0-3) 

For multi-hop `exactInput`, intermediate hops also originate from the router: [5](#0-4) 

The result is a forced choice for the pool admin:

| Router allowlisted? | Effect |
|---|---|
| No | No user can swap via the router, even individually-allowlisted ones |
| Yes | **Every** user can swap via the router, bypassing the per-user allowlist entirely |

There is no configuration that simultaneously permits router-mediated swaps and enforces per-user restrictions, because the extension never sees the real end-user address.

The project's own audit-target document explicitly flags this concern: [6](#0-5) 

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension` to restrict swaps to KYC'd counterparties, whitelisted market-makers, or any other curated set of addresses loses that protection entirely the moment the router is allowlisted. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) targeting the restricted pool and the extension will pass, because it sees only the router's address. LP funds in the pool are exposed to the full universe of traders the allowlist was meant to exclude, with direct potential for LP principal loss through adversarial or uninstructed trading.

---

### Likelihood Explanation

The router is a public, immutable periphery contract. Any pool that wants to support standard user-facing swap UX must allowlist it. The bypass is therefore reachable by any user on any pool that (a) has a `SwapAllowlistExtension` configured and (b) has allowlisted the router — a combination that is the natural steady-state for a "restricted but usable" pool. No special privileges, flash loans, or unusual token behavior are required; a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must gate on the **original end-user**, not the immediate pool caller. Two complementary fixes:

1. **Pass the real user through the router.** The router already stores the original `msg.sender` in transient storage as the payer. Expose it in the `callbackData` or a dedicated transient slot and have the pool forward it as a separate `originator` field to extensions.

2. **Check `sender` in the extension only when the caller is not a known router.** Alternatively, the extension can maintain a registry of trusted routers and, when `sender` is a known router, require the `extensionData` to carry a signed or pre-approved user address.

Until fixed, pool admins should be warned that allowlisting the router nullifies the swap allowlist for all users.

---

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  allowedSwapper[P][router] = true          // router allowlisted so normal users can swap
  allowedSwapper[P][attacker] = false       // attacker explicitly excluded

Attack:
  attacker calls:
    router.exactInputSingle({
      pool: P,
      tokenIn: token0,
      amountIn: X,
      recipient: attacker,
      ...
    })

  router calls pool.swap(recipient=attacker, ...)
    → pool calls E.beforeSwap(sender=router, ...)
    → E checks allowedSwapper[P][router] == true  ✓
    → swap executes, attacker receives output tokens

Result:
  attacker swapped against a pool they were explicitly excluded from.
  The allowlist invariant is broken; LP funds are exposed to the excluded actor.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-165)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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
```

**File:** generate_scanned_questions.py (L657-663)
```python
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
