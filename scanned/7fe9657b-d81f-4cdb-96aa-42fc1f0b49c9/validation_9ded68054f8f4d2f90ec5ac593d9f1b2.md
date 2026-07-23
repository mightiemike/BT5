The call chain is fully traceable and the bypass is real. Here is the complete analysis:

---

### Title
SwapAllowlistExtension checks router address instead of end-user, allowing any user to bypass per-pool swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` = `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is always the **router**, not the end user. If the pool admin allowlists the router, every user — regardless of individual allowlist status — can swap freely.

### Finding Description

The call chain is:

```
attacker → MetricOmmSimpleRouter.exactInputSingle(amountIn=1)
         → pool.swap(recipient, zeroForOne, amountSpecified=1, ...)   [msg.sender = router]
         → MetricOmmPool._beforeSwap(sender=router, ...)
         → ExtensionCalling._callExtensionsInOrder(...)
         → SwapAllowlistExtension.beforeSwap(sender=router, ...)
         → allowedSwapper[pool][router]  ← checked, NOT allowedSwapper[pool][attacker]
```

In `MetricOmmPool.swap()`, the pool passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that same `sender` value directly to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, i.e. `allowedSwapper[pool][router]`: [3](#0-2) 

The extension has no access to the original end-user address. `msg.sender` inside the extension is the pool; `sender` is the router. The end user's identity is permanently lost at the pool boundary.

In `exactInputSingle`, the router calls `pool.swap` directly with no user-identity forwarding: [4](#0-3) 

### Impact Explanation

A pool admin who configures `SwapAllowlistExtension` and allowlists the router (a natural and expected setup — the router is the canonical swap entry point) believes they are controlling which users can swap. In reality, the allowlist only checks the router's address. Every user who calls `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` through the router passes the check unconditionally, regardless of whether they are individually allowlisted. The allowlist is completely non-functional as a per-user gate when the router is the caller. This breaks the core pool curation guarantee the extension is designed to provide.

The `amountIn=1` dust swap confirms the bypass is not gated by swap size — any amount works.

### Likelihood Explanation

The router is the primary intended swap interface. Pool admins who deploy `SwapAllowlistExtension` and want to allow router-based swaps will allowlist the router. This is the expected operational pattern. The bypass is therefore reachable in any realistic deployment of this extension with the router.

### Recommendation

`SwapAllowlistExtension.beforeSwap` must check the **end user**, not the immediate caller. Two options:

1. **Pass the end user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. Requires router cooperation and trust.
2. **Check `tx.origin` as a fallback** (not recommended for general use, but acceptable for allowlist gating in non-flashloan contexts).
3. **Preferred**: Require users to call the pool directly (not through the router) when the allowlist extension is active, and document this constraint clearly. Alternatively, add a dedicated `sender` forwarding field to the extension interface so the router can attest the originating user.

### Proof of Concept

```solidity
// Foundry integration test (conceptual)
function test_allowlistBypass_viaRouter() public {
    // Setup: pool with SwapAllowlistExtension, only router allowlisted
    swapAllowlist.setAllowedToSwap(address(pool), address(router), true);
    // attacker is NOT allowlisted
    assertFalse(swapAllowlist.isAllowedToSwap(address(pool), attacker));

    // Attacker routes through the router with dust amount
    vm.prank(attacker);
    // Succeeds — allowlist checks router address, not attacker
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        amountIn: 1,
        amountOutMinimum: 0,
        recipient: attacker,
        ...
    }));
    // No revert: allowlist bypassed
}
```

The pool's `_beforeSwap` receives `sender=router`, the extension sees `allowedSwapper[pool][router]=true`, and the non-allowlisted attacker's swap executes successfully.

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
