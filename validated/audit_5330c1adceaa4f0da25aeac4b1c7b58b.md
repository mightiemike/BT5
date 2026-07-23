Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the immediate caller of `pool.swap`. When `MetricOmmSimpleRouter` is used, `sender` is the router address, not the end user. If the pool admin allowlists the router (the only way to permit router-mediated swaps), every user on the network can bypass the per-user allowlist by calling the router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` to the pool: [3](#0-2) 

The same applies to `exactInput` (multi-hop): [4](#0-3) 

And `exactOutputSingle`: [5](#0-4) 

The extension has no mechanism to look through the router to the originating user. The pool admin faces an impossible choice: do not allowlist the router (allowlisted users cannot use the standard periphery path) or allowlist the router (the allowlist is completely bypassed for all users). There is no configuration that simultaneously permits router-mediated swaps for allowlisted users and blocks non-allowlisted users.

## Impact Explanation
A pool deployed with `SwapAllowlistExtension` is a curated pool intended to restrict trading to specific addresses. Once the router is allowlisted, any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps on the pool. The allowlist provides zero protection against router-mediated access. This constitutes broken core pool functionality: the configured guard fails open for every user who routes through the supported public periphery contract.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface deployed alongside the protocol. Any user who discovers that the router is allowlisted on a curated pool can immediately exploit this. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices.

## Recommendation
The extension must check the economically relevant actor, not the immediate caller of `pool.swap`. The simplest correct fix: the router encodes `msg.sender` into `extensionData` (appending `abi.encode(msg.sender)`), and the extension decodes and checks that value when `sender` is a known router address. This requires the router to be trusted to supply honest data, which is acceptable since the router is a known, audited contract. Alternatively, the protocol can extend the extension to recognize the router as a transparent forwarder and read the original user from a router-specific field in `extensionData`.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin allowlists only `alice`: setAllowedToSwap(pool, alice, true).
  - Pool admin also allowlists the router: setAllowedToSwap(pool, router, true)
    (required so alice can use the standard periphery path).

Attack (executed by `eve`, a non-allowlisted address):
  1. eve calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  2. Router calls pool.swap(recipient, ...) — pool's msg.sender = router.
  3. Pool calls _beforeSwap(router, ...).
  4. SwapAllowlistExtension.beforeSwap receives sender = router.
  5. Check: allowedSwapper[pool][router] == true → passes.
  6. Swap executes. eve receives output tokens.

Result: eve, a non-allowlisted address, successfully swaps on a curated pool.
The allowlist is completely bypassed through the supported public router path.
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
