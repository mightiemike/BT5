Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Gates on Router Address Instead of Actual Swapper, Enabling Allowlist Bypass - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the user. A pool admin who allowlists the router to support router-based swaps for their allowlisted users inadvertently grants unrestricted swap access to every address, nullifying the allowlist entirely.

## Finding Description
In `SwapAllowlistExtension.beforeSwap`, the check is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (enforced by `onlyPool` on the base class) and `sender` is the first argument forwarded by `MetricOmmPool._beforeSwap`, which is `msg.sender` of the pool's `swap` call:

```solidity
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other swap entry point), the router calls `pool.swap(...)` directly, making the router the `msg.sender` to the pool: [3](#0-2) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores the direct caller (unnamed first `address` parameter) and gates on `owner`, the economically relevant actor: [4](#0-3) 

The swap extension is the odd one out — it identifies the intermediary (the router) rather than the user who initiated the trade.

## Impact Explanation
A pool admin who deploys a `SwapAllowlistExtension`-protected pool and wants allowlisted users to use `MetricOmmSimpleRouter` must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, every address — including non-allowlisted users — can bypass the allowlist by routing through the same router. The curated pool's access control is silently nullified for all swap traffic entering via the router. Non-allowlisted users gain full swap access to a pool designed to restrict trading, which can result in unauthorized extraction of LP value or violation of institutional/regulatory access requirements. This is a broken core pool access-control invariant with direct fund-impact potential.

## Likelihood Explanation
The router is the primary user-facing swap interface. Any pool admin who deploys a `SwapAllowlistExtension`-protected pool and wants their allowlisted users to use the router will naturally allowlist the router — there is no documentation warning against this. The precondition (router allowlisted) is a routine, expected admin action, not a mistake. The bypass is then reachable by any unprivileged address with no further requirements.

## Recommendation
Gate the allowlist on the economically relevant actor, mirroring `DepositAllowlistExtension`. The `recipient` parameter (second argument to `beforeSwap`) is the closest equivalent for swaps. Alternatively, require the actual user identity to be passed through `extensionData` and verified with a signature or trusted-forwarder pattern. At minimum, clearly document that allowlisting the router grants unrestricted swap access to all users, and provide a separate `setAllowedRouter` path distinct from per-user allowlisting.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` attached.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` and `setAllowedToSwap(pool, userB, true)`.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` so that `userA` and `userB` can use `MetricOmmSimpleRouter`.
4. Non-allowlisted `userC` calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The router calls `pool.swap(...)` with `msg.sender = router`; the extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. `userC` successfully swaps in a pool they are not allowlisted for, bypassing the intended access control.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-41)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
```
