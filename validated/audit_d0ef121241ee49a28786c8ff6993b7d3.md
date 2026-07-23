### Title
`SwapAllowlistExtension` Checks Router Address Instead of User Identity, Allowing Any User to Bypass Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the router address is allowlisted (which is required for any router-based trading to function), every user — including those not individually allowlisted — can bypass the per-user swap restriction by calling through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (correct). `sender` is the value the pool passes, which is `msg.sender` from inside `MetricOmmPool.swap()` — i.e., whoever called `pool.swap()` directly. [1](#0-0) 

The pool passes its own `msg.sender` as `sender` to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the end user
    recipient,
    ...
);
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
``` [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

**The broken invariant:** The extension checks `allowedSwapper[pool][router_address]` instead of `allowedSwapper[pool][end_user]`. A pool admin who wants to allow router-based trading must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every user who routes through it — the per-user restriction is completely bypassed.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` parameter (the position owner explicitly passed to `addLiquidity`), not `sender`. [5](#0-4) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd users, protocol partners, or whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The non-allowlisted user executes real swaps against pool liquidity, receiving output tokens and paying input tokens — a direct unauthorized interaction with pool assets. This constitutes a broken core pool functionality and an admin-boundary break where the configured allowlist guard is bypassed by an unprivileged path.

---

### Likelihood Explanation

The trigger requires only that the pool admin has allowlisted the router address (a necessary step for any router-based trading to work on the pool) and that a non-allowlisted user calls any of the router's public `exact*` functions. Both conditions are normal operational states. No special privileges, malicious setup, or non-standard tokens are required. Any user who knows the pool uses `SwapAllowlistExtension` and that the router is allowlisted can exploit this immediately.

---

### Recommendation

The extension must resolve the true end-user identity rather than the direct pool caller. Two approaches:

1. **Check `recipient` instead of `sender`** — for swap allowlists, the economically relevant actor receiving output is `recipient`. However, `recipient` can also be set to a third party, so this is not a complete fix.

2. **Preferred: require the pool to pass the original `msg.sender` through a trusted context** — or redesign the extension to check both `sender` and `recipient`, or require that `sender == recipient` on allowlisted pools.

3. **Simplest safe fix:** Do not allowlist the router address; instead, allowlist individual users and require them to call `pool.swap()` directly. Document this constraint explicitly in the extension.

Alternatively, the `SwapAllowlistExtension` should gate on `recipient` (the address receiving value) rather than `sender` (the address initiating the call), since `recipient` is always the economically benefiting party and cannot be substituted by an intermediary contract without the user's explicit control.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook
  - Pool admin allowlists router address: setAllowedToSwap(pool, router, true)
  - Pool admin does NOT allowlist attacker: allowedSwapper[pool][attacker] = false

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({
       pool: curated_pool,
       recipient: attacker,
       zeroForOne: true,
       amountIn: X,
       ...
     })
  2. Router calls pool.swap(attacker, true, X, ...)
     → pool's msg.sender = router
  3. Pool calls _beforeSwap(sender=router, recipient=attacker, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true ✓
  5. Swap executes; attacker receives output tokens

Result:
  attacker (not individually allowlisted) successfully swaps on a curated pool,
  bypassing the per-user restriction the pool admin intended to enforce.
```

The `SwapAllowlistExtension.beforeSwap` receives `sender = router` and `msg.sender = pool`, so it evaluates `allowedSwapper[pool][router]` — which is `true` — and passes, regardless of who the actual end user is. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
