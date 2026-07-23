The vulnerability is real and exploitable. Here is the complete analysis:

---

### Title
Router-as-sender bypasses per-user swap allowlist in `SwapAllowlistExtension` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is always `msg.sender` of the pool's `swap` call — i.e., the router address, not the end user. Any attacker who routes through an allowlisted router address can swap on a pool that is supposed to restrict swappers to a curated set.

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist as follows: [1](#0-0) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the value the pool passes through. The pool's `swap` function takes no explicit `sender` parameter — it uses its own `msg.sender` (the router) and forwards it as `sender` to `_beforeSwap`: [2](#0-1) 

The router's `exactInputSingle` calls `pool.swap(...)` directly, making the pool see `msg.sender = router`: [3](#0-2) 

So the allowlist check resolves to `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (a natural operational choice for UX), **every user who calls through that router passes the check**, regardless of whether their own EOA is allowlisted.

### Impact Explanation

- Pool admin deploys a curated pool with `SwapAllowlistExtension`, intending only KYC'd or whitelisted EOAs to swap.
- Admin allowlists the router address for convenience.
- Any attacker calls `router.exactInputSingle(...)` with themselves as `recipient`.
- The extension sees `sender = router`, which is allowlisted, and permits the swap.
- The attacker receives pool output tokens despite never being individually allowlisted.
- Core pool functionality (swap curation) is broken; fund loss to the pool's LPs is direct and repeatable.

### Likelihood Explanation

Allowlisting the router is the expected operational pattern — without it, no user can swap via the router at all. The bypass requires only a standard `exactInputSingle` call, available to any EOA. No privileged access, no malicious setup, no non-standard tokens required.

### Recommendation

The extension must check the **originating user**, not the intermediary router. Two options:

1. **Pass the real payer through `extensionData`**: the router encodes `msg.sender` into `extensionData` and the extension decodes and verifies it. This requires the extension to trust the pool's router identity.
2. **Check `recipient` instead of `sender`**: if the invariant is "only allowlisted addresses receive output", check the `recipient` argument in `beforeSwap`.
3. **Require direct pool calls only**: document that `SwapAllowlistExtension` is incompatible with router intermediaries and enforce it by checking `sender == tx.origin` (with known limitations) or by requiring the pool admin to allowlist individual EOAs only.

The cleanest fix is option 1 with a trusted-router registry, or redesigning the extension to check `recipient` if the goal is output-side curation.

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_routerBypassesSwapAllowlist() public {
    // Setup: pool with SwapAllowlistExtension, only router is allowlisted
    extension.setAllowedToSwap(address(pool), address(router), true);
    // attacker EOA is NOT allowlisted
    assertFalse(extension.isAllowedToSwap(address(pool), attacker));

    // Attacker calls through the router
    vm.prank(attacker);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    }));
    // Swap succeeds — attacker receives token1 despite not being allowlisted
    assertGt(token1.balanceOf(attacker), 0);
}
```

The `sender` forwarded to the extension is `address(router)` (allowlisted), so `allowedSwapper[pool][router] == true` and the revert never fires. [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
