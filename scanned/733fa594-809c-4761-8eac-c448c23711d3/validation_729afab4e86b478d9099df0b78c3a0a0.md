### Title
`SwapAllowlistExtension` Checks Router Address Instead of Real Swapper, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps using the `sender` argument forwarded by the pool, which equals `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the actual user. A pool admin who allowlists the router address to enable router-mediated swaps for their curated users simultaneously opens the gate to every user on the internet, completely defeating the per-user allowlist.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap()` directly, making itself `msg.sender` of that call: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][realUser]`. The pool admin faces an impossible choice:

- **Do not allowlist the router** → allowlisted users cannot use the router at all (even though they are individually approved).
- **Allowlist the router** → every user on the internet can bypass the per-user allowlist by routing through the router.

There is no configuration that simultaneously allows router-mediated swaps for approved users and blocks non-approved users.

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, protocol-owned bots, or whitelisted market makers) is fully bypassed the moment the pool admin allowlists the router to support normal UX. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool and execute swaps that the allowlist was designed to block. This constitutes a direct curation failure and, depending on the pool's design, can result in LP principal loss if the allowlist was the primary protection against adversarial trading.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical user-facing entry point for the protocol. A pool admin who wants their allowlisted users to have a normal swap UX will naturally allowlist the router. The flaw is silent — the admin sees no error and has no indication that allowlisting the router opens the gate to everyone. The trigger is a single, reasonable admin action.

### Recommendation

The extension must check the economically relevant actor — the end user — not the intermediary. Two sound approaches:

1. **Pass the original `msg.sender` through the router.** The router already knows the real caller (`msg.sender` at router entry). It can encode the real caller in `extensionData` and the extension can decode and verify it. However, this requires the extension to trust the router, which reintroduces a trust assumption.

2. **Check `sender` against the allowlist only when `sender` is not a known router; otherwise reject.** This is fragile.

3. **Preferred:** The pool's `swap()` signature should accept an explicit `swapper` parameter (the real user), and the router should forward `msg.sender` there. The extension then checks that field. This is the cleanest fix and matches how `addLiquidity` already separates `sender` (payer) from `owner` (position holder).

Until fixed, pool admins must not allowlist the router address on any pool using `SwapAllowlistExtension` if they intend to restrict trading to specific addresses.

### Proof of Concept

```
1. Pool P is deployed with SwapAllowlistExtension.
2. Admin allowlists address Alice: allowedSwapper[P][Alice] = true.
3. Admin also allowlists the router so Alice can use normal UX:
       allowedSwapper[P][router] = true.
4. Attacker Bob (not allowlisted) calls:
       router.exactInputSingle({pool: P, ...})
5. Router calls P.swap(...) — msg.sender = router.
6. Pool calls extension.beforeSwap(sender=router, ...).
7. Extension checks allowedSwapper[P][router] == true → passes.
8. Bob's swap executes on the curated pool despite never being allowlisted.
``` [5](#0-4) [6](#0-5)

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
