Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded from the pool, which is `msg.sender` at the time `pool.swap` is called. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks whether the **router** is allowlisted rather than the actual end user. Any pool admin who allowlists the router to support normal UX simultaneously opens the allowlist gate to every user on the internet, completely defeating per-user access control.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router `msg.sender` to the pool: [3](#0-2) 

The same pattern applies to `exactOutputSingle`: [4](#0-3) 

And to intermediate hops in `exactInput`: [5](#0-4) 

**Exploit flow:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict trading to specific counterparties.
2. Pool admin allowlists the router (`allowedSwapper[pool][router] = true`) to support normal UX.
3. Attacker (not individually allowlisted) calls `router.exactInputSingle(...)`.
4. Router calls `pool.swap(...)` — router is `msg.sender` to the pool.
5. Pool passes `msg.sender` (router) as `sender` to `_beforeSwap`.
6. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Attacker receives full swap output; per-user allowlist is defeated.

Direct swaps by the attacker still revert correctly because `allowedSwapper[pool][attacker]` is `false`. The bypass is exclusive to the router path. No existing guard in the extension or pool detects or prevents this.

## Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to KYC'd addresses, institutional partners, or whitelisted market makers is fully open to any user who routes through the public `MetricOmmSimpleRouter`. Unauthorized traders receive swap output at oracle-derived prices; LP assets are consumed contrary to the pool admin's intended access policy. This constitutes broken core pool functionality (the allowlist-gated swap flow is unusable as designed) and direct loss of LP principal relative to the intended access policy. Severity: **High**.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, publicly deployed periphery entrypoint that users are expected to use. No special privileges, flash loans, or multi-block setup are required — a single router call suffices. Any pool admin who enables router support by allowlisting the router simultaneously triggers the bypass for all users. The bypass is invisible to the pool admin: the extension emits no warning and the allowlist mapping appears correctly configured. Likelihood: **High**.

## Recommendation

The extension must check the economically relevant actor — the end user — not the intermediary contract. The safest near-term fix is to define a standard encoding in `extensionData` for the originating user, have the router populate it (encoding `msg.sender` before calling `pool.swap`), and have `SwapAllowlistExtension.beforeSwap` decode and verify that address when the direct `sender` is a known router. Alternatively, extend the pool's `swap` interface with an explicit `originator` field that the router populates with `msg.sender` and passes through to extensions (cleanest but requires a core interface change).

## Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only allowedUser is allowlisted.
// Pool admin allowlists the router to support normal UX.
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// attacker is NOT individually allowlisted.

// Attacker bypasses the allowlist via the router:
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: attacker,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Swap succeeds. Extension checked allowedSwapper[pool][router] == true.
// Attacker receives token1 output. Per-user allowlist is defeated.

// Confirm direct swap by attacker still reverts (guard works for direct calls):
vm.prank(attacker);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(attacker, true, 1_000e18, 0, "", "");
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
