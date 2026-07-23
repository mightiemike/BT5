Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` binds to `msg.sender` of the `swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. Any pool admin who allowlists the router so that approved users can use it simultaneously opens the allowlist to every unprivileged caller, because the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][end_user]`.

## Finding Description

**Root cause — `SwapAllowlistExtension.beforeSwap` checks the wrong actor:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and gates on it: [1](#0-0) 

`MetricOmmPool.swap` always passes `msg.sender` as the `sender` argument to `_beforeSwap`: [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` is used, the router is the direct caller of `pool.swap()`, so `msg.sender` at the pool is the router address, not the end user: [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. The same applies to `exactOutputSingle`, `exactInput`, and `exactOutput`.

**Contrast with `DepositAllowlistExtension`:**

`DepositAllowlistExtension.beforeAddLiquidity` correctly gates the `owner` argument — the actual position beneficiary — which is invariant to router intermediation: [4](#0-3) 

**The trap for pool admins:**

A pool admin who wants allowlisted users to be able to use the router must call `setAllowedToSwap(pool, router, true)`. The moment they do, `allowedSwapper[pool][router] == true`, and the check at line 37 of `SwapAllowlistExtension` passes for every caller — allowlisted or not — because the router is the entity the extension sees. [5](#0-4) 

## Impact Explanation

Any unprivileged user can execute swaps against a pool configured with `SwapAllowlistExtension` as long as the router is allowlisted. The allowlist is the core access-control mechanism of the extension, intended to restrict which addresses may trade against the pool's LP reserves. Bypassing it allows unauthorized parties to execute swaps at oracle-determined prices, draining LP principal without the pool admin's consent. This is a direct loss path for LP funds and constitutes a broken core pool access-control invariant meeting the Critical/High threshold.

## Likelihood Explanation

The likelihood is high. `MetricOmmSimpleRouter` is the standard user-facing swap entry point. Any pool admin who deploys a `SwapAllowlistExtension` and wants their approved users to be able to use the router must allowlist the router address — this is the natural and expected operational step. Once taken, the bypass is open to every address with no further preconditions, no special permissions, and no additional setup required by the attacker.

## Recommendation

Gate the actual end user, not the intermediary. The most robust fix — mirroring the deposit allowlist pattern — is to check `recipient` instead of `sender` in `beforeSwap`:

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

The router always forwards the caller-supplied `recipient` unchanged to `pool.swap`, so `recipient` is invariant to router intermediation. This mirrors how `DepositAllowlistExtension` uses `owner` (the beneficiary) rather than the intermediary caller. [1](#0-0) 

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]   = true   // alice is the intended gated user
  allowedSwapper[pool][router]  = true   // admin allowlists router so alice can use it
  allowedSwapper[pool][attacker]= false  // attacker is NOT allowlisted

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({
      pool:      pool,
      recipient: attacker,
      ...
  })

  Router calls pool.swap(attacker, ...)
  Pool calls _beforeSwap(msg.sender=router, recipient=attacker, ...)
  Extension checks: allowedSwapper[pool][router] == true  → PASSES
  Swap executes; attacker receives output tokens from LP reserves.

Result:
  attacker bypasses the swap allowlist and executes an unauthorized swap.
  DepositAllowlistExtension would NOT have this problem because it checks
  `owner` (the beneficiary), not `sender` (the intermediary).
```

Foundry test outline:
1. Deploy pool with `SwapAllowlistExtension`.
2. `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)`.
3. As `attacker` (not allowlisted), call `router.exactInputSingle({pool, recipient: attacker, ...})`.
4. Assert the swap succeeds and `attacker` receives output tokens — confirming the bypass.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
