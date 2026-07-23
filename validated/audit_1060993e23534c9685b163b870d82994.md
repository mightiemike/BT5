The vulnerability is real. Here is the complete trace:

**`MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`:** [1](#0-0) 

**`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — `msg.sender` is the pool, `sender` is the router:** [2](#0-1) 

**`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly — the pool sees the router as `msg.sender`, not the original EOA:** [3](#0-2) 

---

### Title
SwapAllowlistExtension Checks Router Address Instead of Originating EOA, Allowing Any User to Bypass Per-User Swap Allowlist via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the value passed by the pool — which is always `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating EOA. This means the extension checks whether the **router** is allowlisted, not whether the **end-user** is allowlisted. Any user who routes through an allowlisted router address bypasses the per-user swap restriction entirely.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230
_beforeSwap(msg.sender, recipient, zeroForOne, ...);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// ExtensionCalling.sol:165
sender,  // = msg.sender of pool.swap = router address
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check is `allowedSwapper[pool][router]`.

When `MetricOmmSimpleRouter.exactInputSingle` is called by a non-allowlisted EOA, the pool sees the router as the caller. If the pool admin has allowlisted the router address (a natural configuration to permit router-mediated swaps), the check passes for **every** user routing through that router, regardless of whether the originating EOA is on the allowlist.

There is no way to simultaneously:
1. Allow specific users to swap through the router, and
2. Block other users from swapping through the router.

The extension is structurally incapable of enforcing per-user restrictions for router-mediated swaps.

### Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of users (e.g., KYC'd addresses, whitelisted counterparties) is fully bypassed by any user routing through `MetricOmmSimpleRouter`. The non-allowlisted user receives token output from the pool's reserves, constituting unauthorized token outflow from a curated pool. The pool admin's curation invariant is broken with direct fund impact: tokens flow to actors the pool was explicitly configured to exclude.

### Likelihood Explanation
`MetricOmmSimpleRouter` is the primary public swap interface. Any pool deployer who configures `SwapAllowlistExtension` and allowlists the router (or any other intermediary contract) to support normal user flows will unknowingly open the pool to all users. The router is a factory-validated, publicly deployed contract, so this path is reachable by any EOA without any special privilege.

### Recommendation
`SwapAllowlistExtension.beforeSwap` should not rely on the `sender` argument passed by the pool (which is the immediate `msg.sender` of `pool.swap`). Instead, the extension should require the originating EOA to be passed explicitly — for example, via `extensionData` — and the router should forward `msg.sender` (the original caller) in `extensionData`. Alternatively, the pool interface should propagate the original transaction initiator (`tx.origin` is unsafe; a signed permit or explicit `originator` field in the swap params is preferable). At minimum, the `SwapAllowlistExtension` documentation must warn that it gates the immediate caller of `pool.swap`, not the end-user, and that router-mediated swaps will always present the router address.

### Proof of Concept
```solidity
// Foundry integration test sketch
function test_routerBypassesSwapAllowlist() public {
    // Pool admin allowlists only the router (to permit router-mediated swaps)
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Non-allowlisted EOA swaps through the router — succeeds (bypass)
    vm.prank(nonAllowlistedEOA);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool), ...
    }));
    // ^^^ does NOT revert — router address passes the allowlist check

    // Same EOA calling pool.swap directly — correctly reverts
    vm.prank(nonAllowlistedEOA);
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    pool.swap(nonAllowlistedEOA, zeroForOne, amount, priceLimit, "", "");
}
```

The swap through the router succeeds because `allowedSwapper[pool][router] == true`, while the direct call reverts because `allowedSwapper[pool][nonAllowlistedEOA] == false`. The allowlist is fully bypassed for all router-mediated swaps.

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
