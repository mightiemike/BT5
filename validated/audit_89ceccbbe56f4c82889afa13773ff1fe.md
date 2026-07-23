All four files confirm the claim. The code path is exactly as described:

1. `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap()` [1](#0-0) 

2. `SwapAllowlistExtension.beforeSwap()` gates on that `sender` argument [2](#0-1) 

3. `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router the `msg.sender` of that call [3](#0-2) 

4. `DepositAllowlistExtension.beforeAddLiquidity()` correctly gates on `owner` (the economic actor), not `sender` — confirming the asymmetry is real [4](#0-3) 

---

Audit Report

## Title
`SwapAllowlistExtension` gates on the direct caller (`sender`) instead of the economic actor, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which `MetricOmmPool.swap()` sets to `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks whether the **router** is allowlisted, not the end-user. Any pool admin who allowlists the router (a natural step to enable router-mediated swaps for allowlisted users) inadvertently grants swap access to every unprivileged address that calls the public router.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the first argument (`sender`) to `_beforeSwap()`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // direct caller of pool.swap(), not the economic actor
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap()` then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When an unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

Here `msg.sender` of `pool.swap()` is the **router contract**, so the extension evaluates `allowedSwapper[pool][router]`. If the admin has allowlisted the router (so that legitimately allowlisted users can trade via the standard periphery), this check passes for **any** caller of the router, including non-allowlisted users.

This is structurally asymmetric with `DepositAllowlistExtension.beforeAddLiquidity()`, which correctly gates on `owner` (the economic actor / position owner), not `sender` (the payer/caller):

```solidity
// DepositAllowlistExtension.sol L38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

No existing guard in the router or pool prevents this bypass. The router stores the original `msg.sender` in transient storage for payment purposes (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)`) but never forwards it to the pool or extension as the economic actor.

## Impact Explanation
A pool admin who configures `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd users, institutional partners, trusted market makers) and also allowlists `MetricOmmSimpleRouter` inadvertently opens the gate to all users. Any unprivileged address can call `router.exactInputSingle()` or `router.exactInput()`, causing the extension to see `sender = router` (allowlisted) and pass the guard. The pool's curation policy is silently nullified, and non-allowlisted users can execute swaps against LP capital that was deployed under the assumption of a restricted counterparty set. This constitutes a broken core pool access-control invariant and an admin-boundary break reachable by an unprivileged path.

## Likelihood Explanation
The only prerequisite is that the pool admin allowlists the router — a natural and expected configuration step when the admin wants allowlisted users to be able to use the standard periphery. The admin has no on-chain signal that doing so opens the gate to all router users, because the deposit allowlist (which the admin likely uses as a mental model) correctly gates on the economic actor regardless of the caller. The attack requires no special privileges, no flash loans, and no non-standard token behavior. Any unprivileged address can execute it repeatably. Likelihood is **Medium**.

## Recommendation
`SwapAllowlistExtension.beforeSwap()` should gate on the `recipient` argument (the address that receives output tokens and is the economic beneficiary of the swap) rather than `sender` (the direct caller), mirroring how `DepositAllowlistExtension` gates on `owner` rather than `sender`:

```solidity
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Alternatively, the router should forward the original `msg.sender` via `extensionData` so extensions can gate on the true initiator, and the pool's NatDoc should explicitly warn that allowlisting the router grants access to all router users.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  alice  → allowlisted swapper (allowedSwapper[pool][alice] = true)
  router → allowlisted swapper (admin adds this so alice can use the router)
  bob    → NOT allowlisted

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})
    → router calls pool.swap(recipient=bob, ...)
    → msg.sender of pool.swap() = router
    → pool calls extension.beforeSwap(sender=router, recipient=bob, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes; bob trades on a pool he was never meant to access

Result:
  bob bypasses the per-user allowlist by routing through the public router.
  LP capital deployed under a restricted-counterparty assumption is now
  accessible to any unprivileged address.
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
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
  }
```
