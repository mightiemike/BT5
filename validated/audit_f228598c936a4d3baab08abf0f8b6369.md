Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass Per-User Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which the pool sets to `msg.sender` of the `pool.swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract address, not the end user. If the pool admin allowlists the router to enable router-based trading, every user — including those not individually allowlisted — can bypass the per-user swap allowlist by routing through the router.

## Finding Description

**Root cause in `SwapAllowlistExtension.beforeSwap`:**

The extension checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the extension's caller) and `sender` is the first argument passed by the pool: [1](#0-0) 

**What the pool passes as `sender`:**

`MetricOmmPool.swap` passes its own `msg.sender` (the direct caller of `pool.swap`) as the `sender` argument to `_beforeSwap`: [2](#0-1) 

**What the router passes as `msg.sender` to the pool:**

All four router entry points call `pool.swap` directly, so the pool always sees `msg.sender = address(router)`:

- `exactInputSingle`: [3](#0-2) 
- `exactInput`: [4](#0-3) 
- `exactOutputSingle`: [5](#0-4) 
- `exactOutput`: [6](#0-5) 

**Result:** The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`. A pool admin who allowlists the router to enable router-based trading inadvertently opens the pool to every user who calls through the router, regardless of individual allowlist status.

**Contrast with `DepositAllowlistExtension`:** The deposit extension correctly checks `owner` (the position owner), which is passed through unchanged regardless of who the payer is. The swap extension has no equivalent "owner" concept — it can only see the direct caller of `pool.swap`. [7](#0-6) 

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, whitelisted market makers, or protocol-internal actors) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The pool admin cannot selectively allow specific users to swap via the router — allowlisting the router is all-or-nothing. Any non-allowlisted address can execute live swaps against the pool's oracle-priced liquidity, violating the pool's curation invariant and potentially draining LP assets through unauthorized trading activity. This constitutes broken core pool functionality causing loss of funds and an admin-boundary break where the allowlist restriction is bypassed by an unprivileged path.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary production swap entry point for end users. A pool admin who configures `SwapAllowlistExtension` and also wants router-based trading to work **must** allowlist the router — there is no other mechanism. This is not a hypothetical misconfiguration; it is the only operational path that enables both features simultaneously. Any pool that has both `SwapAllowlistExtension` and the router allowlisted is fully bypassed for all router users.

## Recommendation

The `SwapAllowlistExtension` must gate on the actual end user, not the intermediary router. The cleanest fix is to have the router encode `msg.sender` (the end user) into `extensionData` before calling `pool.swap`, and have the extension decode and check this value. This requires a trusted encoding convention between the router and the extension. Alternatively, introduce a transient storage slot in the router that exposes `msg.sender` (the end user) in a standardized way, readable by extensions.

## Proof of Concept

```
Setup:
  - Pool P has SwapAllowlistExtension E configured as beforeSwap hook.
  - Pool admin allowlists router R: allowedSwapper[P][R] = true.
  - Alice (address A) is NOT individually allowlisted: allowedSwapper[P][A] = false.

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...}).
  2. Router calls P.swap(recipient, ...) with msg.sender = R (router).
  3. Pool calls _beforeSwap(sender=R, ...).
  4. Extension checks allowedSwapper[P][R] → true → passes.
  5. Alice's swap executes against the curated pool.

Expected: revert NotAllowedToSwap (Alice is not allowlisted).
Actual:   swap succeeds (router is allowlisted, Alice bypasses the guard).
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L136-137)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L165-181)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );
```
