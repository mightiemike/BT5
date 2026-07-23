Audit Report

## Title
Router-Mediated Swap Allowlist Bypass in `SwapAllowlistExtension` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is always `msg.sender` of the `pool.swap()` call — the router, not the originating EOA. When the router is allowlisted, any unprivileged EOA can call `MetricOmmSimpleRouter.exactInputSingle` and trade against a restricted pool, completely defeating the per-pool swap allowlist.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the immediate caller: [4](#0-3) 

The allowlist check therefore resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][attacker]`. Allowlisting the router — the natural operational choice to enable normal trading — implicitly allowlists every EOA that calls the router. No existing guard inspects the originating EOA; the extension has no access to `tx.origin` and the router does not forward `msg.sender` into `extensionData`.

## Impact Explanation

Pool curation is completely broken. The pool admin's intent — restricting swaps to a vetted set of addresses — is defeated by any EOA routing through the allowlisted `MetricOmmSimpleRouter`. Non-allowlisted users can extract value from LPs who deposited under the assumption that only vetted counterparties could trade. This constitutes broken core pool functionality and direct loss of LP principal, satisfying the "Broken core pool functionality causing loss of funds" impact criterion.

## Likelihood Explanation

High. Allowlisting the official `MetricOmmSimpleRouter` is the expected operational pattern for any pool that wants to permit normal trading. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — just calling a public router function. The condition is trivially reachable by any unprivileged EOA.

## Recommendation

The extension must verify the originating user, not the immediate caller. The cleanest fix without changing the core pool/extension interface is to add a per-user allowlist inside `MetricOmmSimpleRouter` that gates `exactInputSingle`/`exactInput`/`exactOutputSingle`/`exactOutput` by `msg.sender`. The pool-level extension allowlists only the router; the router enforces per-user restrictions. Alternatively, routers can encode `msg.sender` into `extensionData` and the extension decodes and checks it, requiring pool admins to allowlist EOAs rather than routers.

## Proof of Concept

```solidity
// Foundry integration test sketch
function test_routerBypassesSwapAllowlist() public {
    // Setup: pool with SwapAllowlistExtension, router is allowlisted, attacker is not
    swapAllowlist.setAllowedToSwap(address(pool), address(router), true);
    // allowedSwapper[pool][attacker] == false (default)

    // Attacker calls router — pool sees sender=router, check passes
    vm.prank(attacker);
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: attacker,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: 1e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    }));
    // Assert: swap succeeded despite attacker not being in allowedSwapper
}
```

The `exactInputSingle` call succeeds because `allowedSwapper[pool][router] == true` and the extension never inspects the attacker's address.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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
  }
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
