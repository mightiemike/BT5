Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of original user, enabling complete allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` as seen by the pool — the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the originating user. Any pool admin who allowlists the router to enable router-mediated access for approved users simultaneously opens the pool to every unprivileged user on the internet, because the allowlist check never inspects the original caller's address.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to `_beforeSwap`:**

`MetricOmmPool.swap` captures `msg.sender` at the point of the external call and forwards it as the `sender` argument: [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension:** [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`:**

`msg.sender` here is the pool, and `sender` is whoever called `pool.swap()`. The check is therefore `allowedSwapper[pool][immediateCallerOfSwap]`: [3](#0-2) 

**Step 4 — `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` inside the pool:** [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Root cause:** The pool has no mechanism to propagate the original EOA through the call stack. The router stores the payer in transient storage (`_getPayer()`) for callback purposes only; it is never passed to the pool or to extensions. The allowlist extension therefore receives the router's address as `sender` for every router-mediated swap, regardless of who initiated the transaction.

**Why existing guards fail:** There is no secondary check in `SwapAllowlistExtension` that falls back to `tx.origin` or reads any originator field. The only guard is `allowedSwapper[msg.sender][sender]`, and `sender` is structurally always the router when the router is used.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (KYC'd counterparties, institutional LPs, whitelisted market makers) provides **no effective access restriction** once the router is allowlisted. The admin faces an inescapable dilemma:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | All router-mediated swaps revert, even for allowlisted users |
| **Allowlist the router** | Every user on the internet bypasses the allowlist via the router |

Any anonymous EOA or contract can call `exactInputSingle` on the public, permissionless `MetricOmmSimpleRouter` and execute swaps against the restricted pool. This constitutes a complete bypass of the pool's access-control layer — unauthorized parties can drain liquidity at oracle prices, extract LP value, or execute trades the pool admin explicitly intended to prohibit. This is a direct loss of LP principal and a broken core pool invariant (admin-configured access control bypassed by an unprivileged path).

## Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract requiring no special role, token balance, or prior interaction. Any EOA can call it in a single transaction with only the pool address as input. The bypass is unconditional once the router is allowlisted — no race condition, no frontrunning, no setup beyond knowing the pool address. The scenario (admin allowlists router to serve approved users) is the natural and expected operational configuration, making exploitation near-certain in any real deployment.

## Recommendation

The extension must receive the **original user's address**, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Pass an `originator` field through the pool interface:** Extend `pool.swap()` to accept an explicit `originator` address that routers populate with `msg.sender` before calling the pool. Extensions receive this field alongside `sender`. This is the cleanest architectural fix.

2. **Encode originator in `extensionData`:** `MetricOmmSimpleRouter` already has `msg.sender` available at call time. It can ABI-encode the original caller into `extensionData`; `SwapAllowlistExtension` decodes and checks it. This requires no pool interface change but relies on the router being the only trusted encoder.

3. **Short-term mitigation:** Document and enforce at the factory level that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` (e.g., revert pool creation that configures both).

## Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured
  allowedSwapper[pool][alice] = true   // alice is the only approved user
  allowedSwapper[pool][router] = true  // admin enables router so alice can use it

Attack (bob, not allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient=bob, ...)          // msg.sender inside pool = router
  → pool calls _beforeSwap(sender=router, ...)
  → ExtensionCalling forwards sender=router to SwapAllowlistExtension.beforeSwap
  → check: allowedSwapper[pool][router] == true  ✓ passes
  → bob's swap executes against the restricted pool

Result: bob, who is not on the allowlist, successfully swaps because
  allowedSwapper[pool][router] = true  (checked)
  allowedSwapper[pool][bob]    = false (never checked)
```

A Foundry integration test can reproduce this by deploying a pool with `SwapAllowlistExtension`, setting `allowedSwapper[pool][alice] = true` and `allowedSwapper[pool][router] = true`, then calling `exactInputSingle` from an address that is not alice and asserting the swap succeeds.

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
