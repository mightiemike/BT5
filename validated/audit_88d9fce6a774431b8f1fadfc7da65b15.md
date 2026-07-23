Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass Swap Allowlist via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If a pool admin allowlists the router to enable router-mediated swaps, the allowlist is bypassed for every user — any unprivileged address can swap against a curated pool by routing through the public router.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes this `sender` and calls the configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly with no forwarding of the original caller — the pool's `msg.sender` is the router: [4](#0-3) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. A pool admin who wants router-mediated swaps to work must allowlist the router address. Once the router is allowlisted, the check passes for every call arriving through the router, regardless of who the actual end user is. The same structural issue applies to the multi-hop `exactInput` path: [5](#0-4) 

And to the `exactOutput` recursive callback path, where `msg.sender` of the inner `pool.swap()` call is again the router: [6](#0-5) 

There is no mechanism in `SwapAllowlistExtension` to decode or verify the true end user from `extensionData`, and no router registry or fallback check exists.

## Impact Explanation

A curated pool (e.g., KYC-gated, institutional-only, or compliance-restricted) that relies on `SwapAllowlistExtension` to restrict who may trade against its LP liquidity is fully bypassed. Any address can swap against the pool at live oracle prices by routing through the public `MetricOmmSimpleRouter`. LP funds are exposed to unauthorized counterparties, and the pool admin's curation policy is silently nullified. This constitutes a direct loss-of-policy control with fund-impacting consequences: unauthorized traders can extract value from LP positions at oracle-determined prices the pool admin intended to restrict to vetted counterparties. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break" impact criteria.

## Likelihood Explanation

The trigger requires the pool admin to allowlist the router address in `SwapAllowlistExtension`. This is a natural and expected operational step: without it, allowlisted users cannot use the standard periphery router at all and must call the pool directly. Any pool that enables router-mediated swaps for its allowlisted users simultaneously opens the bypass to all users. The router is a public, permissionless contract, so no privileged access is needed by the attacker. The condition is likely to be met in any real deployment that intends to support router usage.

## Recommendation

The extension must resolve the true end user rather than the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the original caller through the router**: Modify `MetricOmmSimpleRouter` to encode the original `msg.sender` in `extensionData`, and modify `SwapAllowlistExtension.beforeSwap` to decode and check that address when the immediate `sender` is a known router. This requires a trusted router registry or a signed proof.

2. **Check `sender` against a router registry and fall through to a per-user allowlist**: If `sender` is a registered router, require that the actual end user (passed in `extensionData`) is also allowlisted. This preserves router compatibility without opening the bypass.

The simplest safe default is to never allowlist the router address itself, and instead require users to call the pool directly for allowlisted pools. The NatSpec on `SwapAllowlistExtension` and pool admin tooling should document this constraint explicitly.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls: swapExtension.setAllowedToSwap(pool, address(router), true)
    (to enable router-mediated swaps for allowlisted users)
  admin calls: swapExtension.setAllowedToSwap(pool, allowedUser, true)
    (to allowlist a specific user for direct calls)

Attack:
  attacker (not in allowlist) calls:
    router.exactInputSingle({
      pool: pool,
      recipient: attacker,
      zeroForOne: true,
      amountIn: X,
      ...
    })

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient, ...) [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true → PASSES
        → swap executes at oracle price
        → attacker receives output tokens

  Result: attacker successfully swaps against the curated pool,
          bypassing the allowlist entirely.
          allowedSwapper[pool][attacker] was never set to true.

Foundry test outline:
  1. Deploy pool with SwapAllowlistExtension configured.
  2. Admin calls setAllowedToSwap(pool, router, true).
  3. Attacker (EOA not in allowlist) calls router.exactInputSingle(...).
  4. Assert swap succeeds and attacker receives output tokens.
  5. Assert allowedSwapper[pool][attacker] == false throughout.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
