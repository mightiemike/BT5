Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which resolves to `msg.sender` of the pool's `swap` call. When users swap through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual end user. Any pool admin who allowlists the router to support standard periphery access inadvertently grants every user on the network the ability to bypass the per-pool swap allowlist entirely.

## Finding Description

The sole enforcement point in `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument forwarded by the extension dispatch: [1](#0-0) 

`MetricOmmPool.swap` passes `msg.sender` (whoever called `pool.swap`) as the `sender` argument to `_beforeSwap`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router contract itself the `msg.sender` of that call: [3](#0-2) 

The result is that `beforeSwap` evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. A pool admin who allowlists the router to enable standard tooling collapses the allowlist entirely: every user who calls the router is implicitly allowlisted. Not allowlisting the router breaks router-mediated swaps for all users, including explicitly allowlisted ones. There is no configuration that simultaneously supports router-mediated swaps and enforces a curated user set.

No existing guard in `SwapAllowlistExtension` or `MetricOmmPool` checks the original caller's identity. The `extensionData` field is passed through but never decoded by the extension for identity verification.

## Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router grants every unprivileged user the ability to swap on a pool intended to be restricted. LPs on such a pool accepted counterparty risk only from the allowlisted set; the bypass exposes them to arbitrary counterparties, enabling adversarial flow (informed order flow, directional pressure, sandwich attacks) that the allowlist was designed to exclude. This constitutes a complete failure of the configured access-control boundary and a direct loss-of-LP-value scenario, meeting the admin-boundary break and broken core pool functionality criteria.

## Likelihood Explanation

High. `MetricOmmSimpleRouter` is the canonical user-facing swap entry point. Any pool admin who deploys a curated pool and also wants to support standard wallets, aggregators, or front-ends will allowlist the router. The bypass is then reachable by any unprivileged user with a single `exactInputSingle` call. No special permissions, flash loans, or multi-step setup are required.

## Recommendation

The extension must check the economic actor (end user), not the intermediary. The most robust fix is extension-data forwarding: require the router to encode `abi.encode(msg.sender)` into `extensionData` before calling `pool.swap`, and have `SwapAllowlistExtension.beforeSwap` decode that address as the authoritative swapper identity when `sender` is a known router. Pool admins would then allowlist users, not the router. Alternatively, the router could be modified to always populate `extensionData` with the original caller, and the extension could fall back to `sender` when `extensionData` is empty (for direct callers).

## Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension configured.
2. Pool admin allowlists the router:
       swapExtension.setAllowedToSwap(pool, address(router), true)
3. Pool admin does NOT allowlist userB:
       allowedSwapper[pool][userB] == false
4. userB calls:
       router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}))
5. Router calls pool.swap(...) — msg.sender of pool.swap == address(router).
6. MetricOmmPool._beforeSwap(sender=address(router), ...) is dispatched.
7. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
8. Swap executes. userB has bypassed the allowlist with no special privileges.
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
