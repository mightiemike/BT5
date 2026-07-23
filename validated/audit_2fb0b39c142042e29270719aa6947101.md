Audit Report

## Title
`SwapAllowlistExtension.beforeSwap()` checks router address as swapper, allowing any caller to bypass per-user swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` receives `sender` from the pool, which is `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual end user. If the pool admin allowlists the router address — a natural step to enable router-based swaps for allowlisted users — every unprivileged caller who routes through the router bypasses the per-user swap gate entirely, nullifying the allowlist invariant.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` directly as the first argument to `_beforeSwap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap()
  recipient,
  ...
);
```

`_beforeSwap()` forwards this value unchanged as `sender` to every configured extension via `_callExtensionsInOrder`. `SwapAllowlistExtension.beforeSwap()` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool address and `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
  );
```

So `msg.sender` to the pool is the **router contract**, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

**Bypass path**: A pool admin who wants to enable router-based swaps for allowlisted users will naturally call `setAllowedToSwap(pool, address(router), true)`. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call arriving through the router — regardless of who the actual caller is. Any unprivileged EOA can call `router.exactInputSingle()` and the extension passes.

**Broken-allowlist path (secondary)**: If the admin allowlists individual user addresses but not the router, those allowlisted users who call through the router are incorrectly blocked, because the extension sees the router address and finds no match.

Both paths share the same root cause: `sender` in the swap hook is the direct caller of `pool.swap()` (the router), not the originating human swapper.

## Impact Explanation
A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC-verified traders, institutional market makers, or whitelisted bots). Once the router is allowlisted — a natural and expected admin action — the restriction is nullified for all router-routed calls. Any unprivileged address can execute swaps against the restricted pool, draining LP assets at oracle-derived prices or executing trades the pool admin explicitly intended to prevent. This breaks the core allowlist invariant and constitutes a direct loss of LP principal through unauthorized swap execution, meeting the "Broken core pool functionality causing loss of funds" and "direct loss of user principal" impact criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin who wants to allow router-based swaps for allowlisted users will naturally add the router to the allowlist, triggering the bypass for all users simultaneously. No special privilege is required from the attacker: any EOA calling `exactInputSingle` or `exactInput` through the router is sufficient. The trigger condition (router allowlisted) is a standard, expected operational step, not an exotic misconfiguration.

## Recommendation
The extension must check the actual end user, not the intermediary. Two viable options:

1. **Pass the real user via `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and check it. This requires a convention between router and extension.
2. **Preferred — dedicated originator field**: Add a `payer` or `originator` field to the swap hook parameters that the pool populates from transient storage (the router already stores the payer in transient storage via `_setNextCallbackContext`), giving extensions access to the true initiator without relying on `sender`.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls extension.setAllowedToSwap(pool, address(router), true)
    → intending to allow router-based swaps for allowlisted users
  pool admin does NOT add attacker address to allowlist

Attack:
  attacker (not allowlisted) calls:
    router.exactInputSingle({pool: pool, tokenIn: token0, ...})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient, zeroForOne, ...) [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → extension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ← passes!
        → swap executes, tokens transferred to attacker's recipient

Result:
  Attacker successfully swaps against a pool they are not individually
  authorized to access. The allowlist guard is fully bypassed.
```

Foundry test outline:
1. Deploy pool with `SwapAllowlistExtension` configured.
2. Call `setAllowedToSwap(pool, address(router), true)` as pool admin.
3. Call `router.exactInputSingle(...)` from an unprivileged EOA not in the allowlist.
4. Assert the swap succeeds (no `NotAllowedToSwap` revert) and tokens are transferred. [1](#0-0) [2](#0-1) [3](#0-2)

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
