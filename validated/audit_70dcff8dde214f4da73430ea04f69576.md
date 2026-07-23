Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which the pool sets to `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, that value is the router contract address, not the end user. A pool admin who allowlists the router to enable router-based swaps for their curated users inadvertently opens the pool to every user, defeating the allowlist entirely.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct), and `sender` is the first argument forwarded by the pool, which is set to `msg.sender` at the time `pool.swap()` is called: [2](#0-1) 

Every `MetricOmmSimpleRouter` entry point calls `pool.swap()` directly from the router contract, so `msg.sender` of `pool.swap()` is the router address: [3](#0-2) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`. This creates an impossible choice for pool admins:

- **Allowlist only alice and bob**: Direct swaps work; router calls revert for everyone (router not allowlisted).
- **Allowlist the router**: Alice, bob, and every other user can bypass the allowlist via the router.

There is no configuration that allows specific users to use the router while blocking others.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the position recipient), not `sender` (the direct caller), so it correctly identifies the end user regardless of who calls `addLiquidity`: [4](#0-3) 

The swap extension has no equivalent end-user parameter to check, as the pool only forwards `msg.sender` of `pool.swap()` as `sender`.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router — a natural operational step to support the primary supported periphery — inadvertently allows any unprivileged user to bypass the allowlist. The allowlist invariant (only approved addresses may swap) is broken for all router-mediated swaps. On pools designed to restrict trading to KYC'd counterparties, institutional participants, or specific protocol actors, this allows arbitrary users to execute swaps against LP principal, constituting a broken core pool functionality and potential direct loss of LP assets.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported swap periphery. A pool admin who wants allowlisted users to use the router has no option other than allowlisting the router address. This is a natural, expected operational step. Once the router is allowlisted, the bypass is reachable by any user with no further preconditions — no special privileges, no exotic configuration, no front-running required.

## Recommendation
The `SwapAllowlistExtension` should gate the economically relevant actor — the end user — not the direct caller of `pool.swap()`. Two viable approaches:

1. **Router-forwarded identity via `extensionData`**: The router encodes `msg.sender` (the end user) into `extensionData` for the swap allowlist extension. The extension decodes and checks that address. This requires coordinated changes to both the router and the extension.
2. **Separate router allowlist with per-user check in `extensionData`**: The extension checks `sender` for direct calls and decodes an authenticated user address from `extensionData` for router calls.

At minimum, the extension's NatSpec must document that allowlisting the router grants access to all router users.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Admin calls swapExtension.setAllowedToSwap(pool, alice, true)
   and swapExtension.setAllowedToSwap(pool, bob, true).
3. Alice tries router.exactInputSingle({pool: pool, ...}) → reverts
   NotAllowedToSwap (router not allowlisted). Admin observes this.
4. Admin calls swapExtension.setAllowedToSwap(pool, router, true)
   to fix the router path for alice and bob.
5. Charlie (never allowlisted) calls router.exactInputSingle({pool: pool, ...}).
   Router calls pool.swap(); pool passes sender=router to beforeSwap.
   Extension checks allowedSwapper[pool][router] → true.
   Charlie's swap executes successfully, bypassing the allowlist.
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
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
