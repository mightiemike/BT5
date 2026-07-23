Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Originating User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates on `sender`, which is `msg.sender` at the pool level — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, `sender` is the router contract address, not the originating EOA. If the router is allowlisted (a natural admin action to let allowlisted users access multi-hop or slippage-protected swaps), every non-allowlisted address can bypass the swap gate by routing through the router, executing swaps against restricted LP at oracle-quoted prices.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards `sender` verbatim to every configured extension via `abi.encodeCall`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is the direct pool caller: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the pool see `msg.sender = router`: [4](#0-3) 

The router stores the original payer in transient storage via `_setNextCallbackContext` (line 71) for the payment callback, but never encodes the originating user into `extensionData` before forwarding to the pool. The extension ignores `extensionData` entirely (the last parameter is unnamed and unused). Therefore, `allowedSwapper[pool][router]` is evaluated — the original user's allowlist entry is never consulted.

`DepositAllowlistExtension` avoids this flaw by checking `owner` (the position owner, an economically relevant identity independent of who calls `addLiquidity`), not `sender`: [5](#0-4) 

No analogous identity anchor exists for swaps in the current extension interface.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps against restricted liquidity, receiving tokens from LP positions at oracle-quoted prices that the pool admin intended to reserve for specific parties. This constitutes direct loss of LP principal and a broken core pool invariant (access-controlled swap), meeting the "Broken core pool functionality causing loss of funds" criterion.

## Likelihood Explanation

The only precondition is that the router is allowlisted for the pool. This is a natural and expected admin action: allowlisted users need the router to perform multi-hop or slippage-protected swaps. Any pool that (a) uses `SwapAllowlistExtension` and (b) allowlists the router is immediately exploitable by any address. No privileged role, special token, or malicious setup is required from the attacker's side. The attack is repeatable and requires only a standard router call.

## Recommendation

The extension must gate on the **originating user**, not the intermediary. The preferred fix mirrors `DepositAllowlistExtension`: require the router to encode the original `msg.sender` into `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check that address. A minimal diff:

```diff
- function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
+ function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata extensionData)
      external view override returns (bytes4)
  {
+     address swapper = extensionData.length >= 32 ? abi.decode(extensionData, (address)) : sender;
-     if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
+     if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][swapper]) {
          revert IMetricOmmPoolActions.NotAllowedToSwap();
      }
```

And in `MetricOmmSimpleRouter`, encode `msg.sender` into `extensionData` before forwarding to the pool.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension (beforeSwap order set)
  allowedSwapper[pool][alice]  = true   // alice is the intended user
  allowedSwapper[pool][router] = true   // admin allowlists router so alice can use it
  allowedSwapper[pool][bob]    = false  // bob is NOT allowlisted

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient=bob, ...)   [msg.sender = router]
    → pool calls _beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes — bob receives tokens from restricted LP

Foundry test outline:
  1. Deploy pool with SwapAllowlistExtension configured in beforeSwap order
  2. setAllowedToSwap(pool, alice, true)
  3. setAllowedToSwap(pool, router, true)
  4. vm.prank(bob); router.exactInputSingle(...)
  5. Assert swap succeeds (no NotAllowedToSwap revert) and bob receives output tokens
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
