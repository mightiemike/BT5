Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the actual user. If the pool admin allowlists the router — the only way to permit router-mediated swaps for legitimate users — every unpermissioned user can bypass the allowlist by routing through the same public router.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` at the pool level: [4](#0-3) 

At that point `msg.sender` inside the pool is the router, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The pool admin faces an impossible choice: do not allowlist the router (legitimate allowlisted users cannot use the router at all), or allowlist the router (every unpermissioned user can bypass the allowlist). There is no configuration that simultaneously permits router-mediated swaps for allowlisted users and blocks them for non-allowlisted users. No existing guard in `SwapAllowlistExtension` or `MetricOmmPool` resolves this ambiguity.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd, whitelisted, or otherwise vetted addresses loses that guarantee entirely once the router is allowlisted. Any unpermissioned address can execute swaps at oracle-derived prices against LP liquidity, violating the pool's access-control policy. The wrong value is `allowedSwapper[pool][router]` being evaluated instead of `allowedSwapper[pool][user]`, resulting in unauthorized swap execution against LP liquidity — a direct bypass of a configured access-control guard with fund-impacting consequences.

## Likelihood Explanation

The router is the standard, documented entry point for swaps. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address. The bypass is then available to every public user with no special privileges, no front-running, and no complex setup — a single `exactInputSingle` call suffices. The condition is trivially reachable and repeatable.

## Recommendation

The allowlist must gate the economically relevant actor — the end user — not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the extension to trust the router, which reintroduces a trust assumption.

2. **Trusted forwarder pattern**: The extension distinguishes between direct calls (where `sender` is the user) and router calls (where `sender` is the router), and in the latter case reads the real user from a verified source (e.g., a transient-storage slot written by the router before calling the pool).

The cleanest fix is to redesign the hook signature so the pool passes both the immediate caller and the originating user, or to require that the router always encodes the real user in `extensionData` and the extension verifies the router's identity before trusting that field.

## Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — necessary so that allowlisted users can use the router.
3. Non-allowlisted attacker calls:
     router.exactInputSingle({pool: pool, ...})
   The router calls pool.swap(...); msg.sender at the pool = router.
4. Extension evaluates allowedSwapper[pool][router] == true → passes.
5. Attacker's swap executes against LP liquidity despite never being
   individually allowlisted.
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
