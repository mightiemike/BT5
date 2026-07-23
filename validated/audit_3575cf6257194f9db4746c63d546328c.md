The call chain is fully traceable. Here is the exact flow:

1. `MetricOmmSimpleRouter.exactInputSingle` is called by an EOA (`msg.sender = EOA`)
2. The router calls `pool.swap(recipient, ...)` — at this point `msg.sender` to the pool is the **router address**
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` at line 230 — `msg.sender` here is the router
4. `ExtensionCalling._beforeSwap` forwards `sender = router` to `SwapAllowlistExtension.beforeSwap`
5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`

The check is on the **router address**, not the originating EOA. [1](#0-0) [2](#0-1) 

---

### Title
SwapAllowlistExtension Checks Router Address Instead of Originating EOA, Allowing Any User to Bypass Per-User Swap Allowlist via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of `pool.swap`. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the originating EOA. The allowlist check therefore evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][EOA]`, making the per-user gate ineffective for any swap routed through the router.

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // <-- router address when called via MetricOmmSimpleRouter
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// ExtensionCalling.sol:163-165
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, ...)   // sender == router address
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check is `allowedSwapper[pool][router]`. If the pool admin allowlists the router (a natural action to permit router-based trading), every EOA in existence can swap through the router regardless of whether they are individually allowlisted.

`MetricOmmSimpleRouter.exactInputSingle` stores the originating `msg.sender` only in transient storage for the payment callback — it is never forwarded to the pool or the extension:

```solidity
// MetricOmmSimpleRouter.sol:71
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
```

There is no mechanism in the router or the pool to propagate the originating EOA to the extension.

### Impact Explanation

The `SwapAllowlistExtension` is documented as gating "swap by swapper address, per pool." When the router is used, this invariant is broken: the allowlist gates the router address, not the economic actor. Any EOA can bypass a per-user swap restriction by routing through `MetricOmmSimpleRouter`. The pool admin has no way to enforce individual EOA restrictions on router-mediated swaps using this extension. This breaks the core access-control functionality the extension is designed to provide.

### Likelihood Explanation

The router is the primary intended interface for end-user swaps. Pool admins deploying `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., KYC'd addresses, whitelisted market makers) will naturally allowlist the router to permit router-based access, unknowingly granting unrestricted access to all EOAs. The bypass requires no special privileges — any EOA with token approval on the router can exploit it.

### Recommendation

Pass the originating caller through the extension data or a dedicated field. One approach: the pool should expose a way for the router to forward the originating `msg.sender` (e.g., via `extensionData`), and `SwapAllowlistExtension.beforeSwap` should decode and check that address when present. Alternatively, document clearly that `sender` is the immediate pool caller and that the extension cannot gate individual EOAs when a router is used, and provide a separate router-aware allowlist extension.

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_allowlistBypass_viaRouter() public {
    // Admin allowlists only the router (intending to allow router-based swaps)
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Non-allowlisted EOA (bob) swaps via router — succeeds (bypass)
    vm.prank(bob);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool), ...
    }));

    // Same EOA calls pool.swap directly — reverts (allowlist works for direct calls)
    vm.prank(bob);
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    pool.swap(bob, true, 1000, 0, "", "");
}
```

The direct call reverts because `allowedSwapper[pool][bob]` is false. The router call succeeds because `allowedSwapper[pool][router]` is true, and the pool passes `sender = router` to the extension.

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
