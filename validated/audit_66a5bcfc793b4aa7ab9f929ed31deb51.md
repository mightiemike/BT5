The code confirms all three steps of the claimed call chain. Let me verify the key details:

The full call chain is confirmed by the production code. All three steps are verified:

1. `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap` [1](#0-0) 

2. `ExtensionCalling._beforeSwap` forwards that `sender` value directly to the extension [2](#0-1) 

3. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`, never `allowedSwapper[pool][actual_user]` [3](#0-2) 

---

Audit Report

## Title
SwapAllowlistExtension checks router address instead of actual user — any user can bypass per-user swap allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` uses the `sender` argument — which is `msg.sender` of the pool's `swap` call, i.e., the router — to enforce the per-user allowlist. When a pool admin allowlists the router to support router-based swaps for legitimate users, every user including non-allowlisted ones can bypass the allowlist by routing through `MetricOmmSimpleRouter`. The extension's per-user gate is completely neutralized for all router-mediated swaps.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    zeroForOne,
    ...
);
```

`ExtensionCalling._beforeSwap` encodes this `sender` value and dispatches it to the configured extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

where `msg.sender = pool` and `sender = router`. The evaluated condition is `allowedSwapper[pool][router]`, never `allowedSwapper[pool][actual_user]`.

A pool admin who wants to support router-based swaps for allowlisted users will naturally call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the extension's per-user gate is completely bypassed for every user who calls through the router. The attacker calls `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`; the router calls `pool.swap(...)`; the pool passes `msg.sender = router` to `_beforeSwap`; the extension checks `allowedSwapper[pool][router] = true` and permits the swap regardless of who the actual caller was.

## Impact Explanation

`SwapAllowlistExtension` is the designated mechanism for pool curation — restricting which addresses may swap. When the router is allowlisted (a necessary step to support router-based swaps for legitimate users), the extension becomes entirely ineffective: any address can call `exactInput` or `exactInputSingle` through the router and swap against a pool that is supposed to be restricted. This breaks the core pool curation functionality the extension is designed to provide. The exact wrong value is the `allowedSwapper[pool][sender]` lookup: it resolves to the router's allowlist entry rather than the actual initiating user's entry, causing the extension to return `beforeSwap.selector` (permit) when it should revert with `NotAllowedToSwap`.

## Likelihood Explanation

Any pool that uses `SwapAllowlistExtension` to restrict swappers and allowlists the router so that approved users can swap via the router is vulnerable. This is the natural and expected configuration for a curated pool that also supports router integration. The admin has no way to simultaneously allowlist the router and enforce per-user restrictions using this extension. The attacker requires no special privileges — any address can call the public router functions.

## Recommendation

`SwapAllowlistExtension` should check the actual initiating user, not the immediate caller. One approach: if `sender` is a known router, read the actual user from a trusted source (e.g., a router-provided `extensionData` field that the router stamps with `msg.sender`). Alternatively, document clearly that allowlisting the router grants all router users swap access, and provide a separate per-user enforcement mechanism that does not rely on `sender` when routers are involved.

## Proof of Concept

```solidity
function testFuzz_swapAllowlistBypassViaRouter(bool zeroForOne) public {
    // Setup: pool with SwapAllowlistExtension, router allowlisted, attacker NOT allowlisted
    swapExtension.setAllowedToSwap(address(pool), address(router), true);
    // attacker is NOT in allowedSwapper

    // Attacker routes through router
    vm.prank(attacker);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        zeroForOne: zeroForOne,
        amountIn: 1000,
        amountOutMinimum: 0,
        ...
    }));
    // Succeeds — pool sees msg.sender = router, extension checks
    // allowedSwapper[pool][router] = true, attacker's swap executes
}
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
