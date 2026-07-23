Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the End-User, Allowing Any Caller to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which equals `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end-user. If the pool admin allowlists the router to enable standard UX for permitted users, every unpermissioned user can bypass the allowlist by routing through the router, rendering the allowlist guard inoperative on the primary user-facing entry point.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool via `CallExtension.callExtension`), and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the `pool.swap()` call itself, as confirmed in `MetricOmmPool.swap()`:

```solidity
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)`. At that point, `msg.sender` of `pool.swap()` is the **router contract**, not the end-user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The pool admin faces an impossible choice: allowlisting the router (the natural action to enable standard UX) opens the allowlist to every unpermissioned user; not allowlisting the router breaks router-mediated swaps for all users including permitted ones.

The `DepositAllowlistExtension` does not share this flaw because it gates `owner` (the position recipient), which is a caller-supplied argument that is not substituted by any router intermediary in the `addLiquidity` path.

## Impact Explanation
**Admin-boundary break / broken core pool functionality.** A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., a private OTC pool, a KYC-gated venue, or a pool restricted to a specific market maker) can be fully bypassed by any unpermissioned user routing through `MetricOmmSimpleRouter`. The allowlist guard is rendered inoperative on the public router path. Any swap executed by an unpermissioned user drains pool liquidity at oracle prices, directly impacting LP principal and fee accrual. This meets the "admin-boundary break" and "broken core pool functionality causing loss of funds" criteria.

## Likelihood Explanation
**High.** `MetricOmmSimpleRouter` is the standard periphery entry point. A pool admin enabling a swap allowlist would naturally also allowlist the router to avoid breaking the standard UX for permitted users. The bypass requires no special privileges, no flash loans, and no exotic token behavior — any EOA can call the router. The precondition (router allowlisted) is the expected operational state.

## Recommendation
Pass the end-user identity through the call chain so the extension can gate the correct actor. The preferred approach is to have the router encode `msg.sender` (the end-user) into `extensionData` before forwarding to the pool, and have the extension decode and check that address when `sender` is a known trusted forwarder. Alternatively, require allowlisted users to call the pool directly and document that the allowlist does not function correctly when the router is allowlisted.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension, extensionOrders.beforeSwap = extension1
  - Pool admin calls setAllowedToSwap(pool, router, true)   // natural: "allow the router"
  - Pool admin does NOT allowlist attacker (0xAttacker)

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient=attacker, ...)         // router is msg.sender
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] → true   ✓
  5. Swap executes; attacker receives token output from a restricted pool

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds — allowlist bypassed
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
