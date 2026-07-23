Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which equals `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, the router contract becomes `msg.sender` to the pool. For any legitimate user to use the router with a restricted pool, the pool admin must allowlist the router address — but once the router is allowlisted, every user (including explicitly excluded ones) can bypass the restriction by routing through the same public contract.

## Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` to the pool — not the end user: [4](#0-3) 

The root cause is that `sender` in `beforeSwap` represents the immediate caller of the pool, not the originating user. The extension has no mechanism to distinguish between the router acting on behalf of an allowlisted user versus an unauthorized user. Once the router is allowlisted (the only way legitimate users can use the router with this pool), the check `allowedSwapper[pool][router] == true` passes for every caller of the router, regardless of whether that caller is on the allowlist.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, institutional traders) is fully open to any user who routes through the public `MetricOmmSimpleRouter`. The allowlist guard is rendered inoperative for all router-mediated swaps. This constitutes a broken core pool access-control functionality: the admin-configured restriction is silently voided, allowing unauthorized users to execute swaps in a pool designed to be restricted. This maps to the "Admin-boundary break" allowed impact: an unprivileged path bypasses a pool admin access-control check.

## Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless periphery contract — any user can call it. The bypass requires no special privileges, no flash loans, and no contract deployment. The only precondition is that the pool admin has allowlisted the router, which is the only way legitimate users can use the router with this pool, making the bypass automatically available whenever the pool is usable via the router. The attack is trivially repeatable on every swap.

## Recommendation

The extension must gate the end user, not the intermediary. Two complementary fixes:

1. **In `SwapAllowlistExtension.beforeSwap`**: ignore the `sender` argument and instead require callers to supply the real user identity in `extensionData`, verified with a signature or trusted forwarder pattern.
2. **Alternatively**: the router should forward the original `msg.sender` as part of `extensionData`, and the extension should decode and verify that identity rather than trusting the pool-level `sender`.

A simpler short-term mitigation is to document that allowlisting the router is equivalent to `allowAllSwappers = true`, and require pool admins to use direct pool calls (not the router) for restricted pools.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension, allowAllSwappers[pool] = false
  - Pool admin allowlists Alice: allowedSwapper[pool][alice] = true
  - Pool admin allowlists the router: allowedSwapper[pool][router] = true
    (required so Alice can use the router)

Attack:
  - Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient=bob, ...)  →  msg.sender to pool = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true  ✓
  - Bob's swap executes successfully despite not being on the allowlist

Result:
  - SwapAllowlistExtension is bypassed for every user who routes through the public router.
  - The pool admin's intended access restriction is silently voided.
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
