Audit Report

## Title
SwapAllowlistExtension Bypassed via Router: Extension Checks Router Address Instead of End User — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool, which is `msg.sender` of the `pool.swap()` call — the router contract address, not the end user. When a pool admin allowlists `MetricOmmSimpleRouter` to enable router-mediated swaps, every user who routes through that public, permissionless contract passes the allowlist check regardless of individual authorization, completely defeating the allowlist's purpose.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` value directly into the encoded extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the address received from the pool: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract address: [4](#0-3) 

`MetricOmmSimpleRouter` has no per-user access control — it is a fully public, permissionless contract. A pool admin who wants router-mediated swaps to work must add the router to `allowedSwapper`. Once `allowedSwapper[pool][router] = true`, the check passes for every caller of the router because the extension never inspects the actual end user.

The test suite confirms this binding: the allowlist is set for `callers[0]` (the intermediate `TestCaller` wrapper), not for `users[0]` (the human address), and the swap still succeeds when called by `users[0]`: [5](#0-4) 

## Impact Explanation

Any user — including those explicitly excluded from the allowlist — can bypass the swap gate on a curated pool by routing through `MetricOmmSimpleRouter`. The allowlist's purpose (KYC enforcement, institutional-only pools, regulatory compliance) is entirely defeated. The attacker receives real swap output tokens from a pool they are not permitted to trade on. This is a direct, fund-impacting policy bypass on every pool that combines `SwapAllowlistExtension` with router allowlisting. This falls under the "Admin-boundary break" allowed impact: an unprivileged path bypasses the pool admin's intended access control.

## Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap entry point in the protocol.
- A pool admin who deploys an allowlisted pool and wants standard router support will naturally allowlist the router address — the flaw is non-obvious because the admin believes they are enabling the router while still gating individual users.
- No privileged access, special tokens, or malicious setup is required. Any EOA can call the router.
- The bypass is repeatable and deterministic on every pool in this configuration.

## Recommendation

The extension must gate the economically relevant actor — the end user — not the intermediate router. Two sound approaches:

1. **Pass the original caller through the pool**: Have `MetricOmmPool.swap()` accept an explicit `originator` parameter (verified against `msg.sender` or a trusted forwarder registry) so the router can forward the real user address. The extension then checks that address.
2. **Gate at the router level**: Add per-pool, per-user access control inside `MetricOmmSimpleRouter` itself, and do not allowlist the router in the extension. The extension then only gates direct pool callers.

Do not use `tx.origin` — it breaks contract-to-contract composability and introduces phishing risks.

## Proof of Concept

```
Setup:
  pool deployed with SwapAllowlistExtension (extension slot 2 active for beforeSwap)
  allowedSwapper[pool][router] = true   ← admin allowlists router to enable router swaps
  allowedSwapper[pool][attacker] = false ← attacker is explicitly excluded

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({
      tokenIn: token0, tokenOut: token1, pool: pool,
      recipient: attacker, amountIn: X, ...
  })

  Router calls pool.swap(recipient=attacker, ...)
  Pool: msg.sender = address(router) → _beforeSwap(sender=router, ...)
  SwapAllowlistExtension.beforeSwap:
      allowedSwapper[pool][router] == true → check passes
  Swap executes; attacker receives token1 output.

Result:
  attacker, not in the allowlist, successfully swaps on a curated pool.
  The allowlist invariant is broken with no privileged action required.
```

Foundry test: deploy pool with `SwapAllowlistExtension`, set `allowedSwapper[pool][router] = true`, set `allowedSwapper[pool][attacker] = false`, call `router.exactInputSingle` from `attacker` address, assert swap succeeds and attacker receives output tokens.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
