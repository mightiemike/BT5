Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address as Swapper, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` sets to `msg.sender` — the direct caller of the pool. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router to support standard periphery usage inadvertently grants every user on-chain the ability to bypass the allowlist entirely, defeating the purpose of the extension.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // always the direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

Here `msg.sender` is the pool address and `sender` is whoever called `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

The pool's `msg.sender` is the **router**, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` with `msg.sender = router`. [4](#0-3) 

This creates an irreconcilable dilemma:
- **Router NOT allowlisted**: every allowlisted user who calls through the router is blocked.
- **Router IS allowlisted** (the natural fix): `allowedSwapper[pool][router] == true` passes for **every** user routing through the router, regardless of individual allowlist status.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., institutional market makers, KYC'd addresses) loses that restriction entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool's liquidity at oracle-derived prices. LP providers who deposited under the assumption of a curated counterparty set suffer unrestricted adverse selection, leading to direct loss of LP principal. This matches the **admin-boundary break** and **broken core pool functionality causing loss of funds** impact categories.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical periphery entry point for swaps. Pool admins who configure `SwapAllowlistExtension` and also want to support the standard router will naturally allowlist the router address. The bypass requires no special privileges, no flash loans, and no complex setup — any user with a standard ERC-20 approval to the router can exploit it in a single transaction.

## Recommendation
The extension must gate on the economically relevant actor — the end user — not the intermediary. Two viable approaches:

1. **Pass the original user through the router**: Modify `MetricOmmSimpleRouter` to encode `msg.sender` (the real user) into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and verify it alongside the router's identity.

2. **Dedicated router-aware allowlist**: Introduce a protocol-level convention where the router populates a standardized `extensionData` identity field with `msg.sender` before forwarding to the pool, and the extension verifies both the router's allowlist status and the embedded user identity.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT allowlist attacker address

Attack:
  1. attacker (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: curated_pool,
           recipient: attacker,
           zeroForOne: true,
           amountIn: X,
           ...
       })
  2. Router calls pool.swap(attacker, true, X, ...) with msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
  5. Swap executes; attacker receives tokens from the curated pool

Result:
  - attacker, who is not individually allowlisted, successfully swaps
  - SwapAllowlistExtension policy is completely bypassed
  - LP providers in the curated pool are exposed to unrestricted counterparties
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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
