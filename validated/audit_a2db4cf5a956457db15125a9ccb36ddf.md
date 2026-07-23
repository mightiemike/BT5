Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address as Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original EOA. If the pool admin allowlists the router — the natural step to let allowlisted users access the router — any unprivileged user can bypass the allowlist entirely by routing through the same public contract.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded from the pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`:

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

So the extension receives `sender = address(router)`, not the original EOA. The allowlist lookup becomes `allowedSwapper[pool][router]`. A pool admin who wants allowlisted users to use the router must allowlist the router address. The moment they do, every unprivileged user can bypass the allowlist by calling the same public router.

## Impact Explanation
Any user can trade on a curated, allowlist-gated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`). The pool admin's access-control policy is silently voided. This matches the allowed impact gate: *"Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path."* Depending on the pool's purpose, this enables unauthorized participants trading on KYC-gated or compliance-restricted pools, or front-runners and arbitrageurs trading on pools designed to exclude them, causing direct LP value leakage through adverse selection.

## Likelihood Explanation
The precondition — the router being allowlisted — is the natural and expected configuration for any pool that wants its allowlisted users to access the router. The protocol ships the router as the primary user-facing entry point. A pool admin who does not allowlist the router breaks the UX for their own allowlisted users. The bypass is therefore reachable on any production pool that correctly integrates the router. No special privileges or unusual conditions are required; any unprivileged EOA can execute the attack.

## Recommendation
The `SwapAllowlistExtension` must gate the original economic actor, not the immediate caller. The most robust fix is to have the router encode `msg.sender` into `extensionData`, and have the extension decode and check it when the caller is a known router. Alternatively, add an `originalSender` field to the extension interface so the pool can forward the true initiator. Using `tx.origin` is fragile and should be avoided. Until fixed, pool admins using `SwapAllowlistExtension` must not allowlist the router address, accepting that allowlisted users cannot use the router.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls swapExtension.setAllowedToSwap(pool, address(router), true)
    → router is allowlisted so that allowlisted users can use it

Attack:
  attacker = address not in allowedSwapper[pool]
  attacker calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(...) with msg.sender = router
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true
    → hook returns selector, swap proceeds
  attacker receives output tokens from the allowlist-gated pool
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
