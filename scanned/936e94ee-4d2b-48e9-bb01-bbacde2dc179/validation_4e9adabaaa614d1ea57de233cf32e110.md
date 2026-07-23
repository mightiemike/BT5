### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual user. A pool admin who allowlists the router to enable router-based swaps for their curated pool inadvertently grants every user on-chain the ability to bypass the per-user allowlist entirely.

---

### Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the `sender` parameter in the ABI-encoded call to the extension: [2](#0-1) 

**Step 2 — Extension checks `sender`, which is the router when routing through periphery.**

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (used as the pool key — correct), and `sender` is the first parameter — the direct caller of `pool.swap()`.

**Step 3 — Router is the direct caller of `pool.swap()`.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

The pool's `msg.sender` is therefore the router address. The extension receives `sender = router`, not the actual end user who called `exactInputSingle`.

**Step 4 — The allowlist check resolves against the router, not the user.**

The extension evaluates `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (a natural step to enable router-based swaps for their curated pool), the check passes for **every** user who routes through it, regardless of whether that user is individually allowlisted.

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all of them call `pool.swap(...)` directly from the router context. [5](#0-4) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise approved addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The pool admin cannot simultaneously (a) allow allowlisted users to use the standard router and (b) block non-allowlisted users from using the same router — the two goals are structurally incompatible under the current design. Any non-allowlisted user can execute swaps against the restricted pool, draining LP assets at oracle-quoted prices without authorization.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the documented, supported periphery swap path. A pool admin configuring a curated pool will naturally allowlist the router to give their approved users a standard UX. The moment the router is allowlisted, the bypass is open to everyone. No privileged access, no malicious setup, and no non-standard token behavior is required — only a call to `exactInputSingle` with the target pool address.

---

### Recommendation

The extension must check the actual end user, not the intermediary. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `sender` only for direct pool calls; require a trusted forwarder pattern for router calls**: The extension distinguishes between direct callers and router-mediated calls and applies the allowlist to the decoded originator in both cases.

The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position owner explicitly passed by the caller), which the liquidity adder correctly sets to the actual depositor. [6](#0-5) 

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is approved
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted for UX
  - bob is NOT allowlisted.

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({
         pool: restrictedPool,
         recipient: bob,
         zeroForOne: true,
         amountIn: X,
         ...
     })
  2. Router calls pool.swap(bob, true, X, ...)
     → pool.msg.sender = router
     → _beforeSwap(sender=router, ...)
     → extension checks allowedSwapper[pool][router] → true ✓
  3. Swap executes. bob receives tokens from the restricted pool.

Expected: revert NotAllowedToSwap (bob is not allowlisted)
Actual:   swap succeeds — allowlist is bypassed
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at: [7](#0-6) 

combined with the pool's actor-forwarding at: [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
