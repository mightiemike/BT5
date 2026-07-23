Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of actual swapper, allowing full allowlist bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which `MetricOmmPool.swap()` sets to `msg.sender` — the immediate caller of the pool. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the router's address rather than the actual user's address. If the pool admin allowlists the router (the natural action to enable router-based swaps on a curated pool), every address on the network can bypass the allowlist entirely by routing through `MetricOmmSimpleRouter`.

## Finding Description
**Pool passes `msg.sender` as `sender` to the extension:**

In `MetricOmmPool.swap()`, `msg.sender` is forwarded as the `sender` argument to `_beforeSwap`: [1](#0-0) 

When the call originates from `MetricOmmSimpleRouter.exactInputSingle()`, `msg.sender` at the pool is the router contract, not the end user.

**Extension checks the wrong actor:**

`SwapAllowlistExtension.beforeSwap` uses this `sender` value to look up the allowlist: [2](#0-1) 

So `allowedSwapper[pool][sender]` resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Router calls `pool.swap()` directly, becoming `msg.sender`:** [3](#0-2) 

**Contrast with `DepositAllowlistExtension`:**

`DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the actual position owner passed explicitly through the call chain), not `sender`: [4](#0-3) 

This confirms the design intent was to check the actual economic actor. `SwapAllowlistExtension` has no equivalent stable identity — `sender` is the only identity available and it resolves to the router.

**The mismatch:**

| Path | `sender` seen by extension | Allowlist check |
|---|---|---|
| User → `pool.swap()` directly | actual user address | correct |
| User → `router.exactInputSingle()` → `pool.swap()` | router address | wrong actor |

## Impact Explanation
A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a whitelist of counterparties. To allow those counterparties to use the standard router, the admin calls `setAllowedToSwap(pool, address(router), true)`. This single allowlist entry grants every address on the network the ability to swap on the curated pool by routing through `MetricOmmSimpleRouter`. The allowlist is completely defeated — non-allowlisted users trade freely on a pool designed to exclude them, causing direct loss of curation policy and potentially unauthorized price exposure or LP value extraction on restricted pools. This constitutes broken core pool functionality (access control) causing loss of funds and unusable curated-pool swap flows.

## Likelihood Explanation
- `SwapAllowlistExtension` is a production periphery extension explicitly designed for curated pools.
- `MetricOmmSimpleRouter` is the primary user-facing swap interface for the protocol.
- Allowlisting the router is the natural and expected admin action to enable router-based swaps on a curated pool.
- No special privileges, malicious setup, or non-standard tokens are required. Any unprivileged user can exploit this by calling `router.exactInputSingle()` targeting the curated pool.
- The secondary scenario (admin does not allowlist the router) breaks the primary swap interface for all allowlisted users.

## Recommendation
`SwapAllowlistExtension` must not rely on `sender` (the immediate pool caller) for identity. The most robust fix mirrors the `DepositAllowlistExtension` pattern: require the router to embed the originating `msg.sender` in `extensionData`, and have the extension decode and verify it. Alternatively, the pool could be modified to pass a stable "originating user" identity through the call chain, analogous to how `owner` is passed for liquidity operations.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Admin calls `swapExtension.setAllowedToSwap(pool, address(router), true)` to enable router-based swaps.
3. A non-allowlisted `attacker` calls:
   ```solidity
   router.exactInputSingle(ExactInputSingleParams({
       pool: curatedPool,
       tokenIn: token0,
       tokenOut: token1,
       zeroForOne: true,
       amountIn: 1_000,
       amountOutMinimum: 0,
       recipient: attacker,
       deadline: block.timestamp,
       priceLimitX64: 0,
       extensionData: ""
   }));
   ```
4. The pool calls `_beforeSwap(msg.sender=router, ...)`. The extension checks `allowedSwapper[pool][router]` → `true`. The swap executes.
5. `attacker` successfully trades on a pool that was supposed to block them. The allowlist is fully bypassed.

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
