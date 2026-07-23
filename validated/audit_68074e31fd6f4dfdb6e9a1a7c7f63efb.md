All cited code is confirmed in the repository. The call chain is fully traceable and accurate:

1. `MetricOmmPool.swap()` passes `msg.sender` (the router) as `sender` to `_beforeSwap` — confirmed at lines 230–240.
2. `ExtensionCalling._beforeSwap` forwards that `sender` value unchanged into the extension call — confirmed at lines 149–177.
3. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, i.e., `allowedSwapper[pool][router]` — confirmed at line 37.
4. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no user-identity forwarding — confirmed at lines 71–80.

The extension's NatSpec states it "Gates `swap` by swapper address, per pool," but when the router is the caller, it gates by router address, not the end user. The bypass is real, the preconditions are non-malicious (allowlisting the router is the expected operational pattern), and the impact is a complete failure of the per-user access control the extension is designed to enforce.

---

Audit Report

## Title
SwapAllowlistExtension checks router address instead of end-user, allowing any user to bypass per-pool swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` equal to `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is always the router contract, not the end user. If the pool admin allowlists the router — the canonical and expected swap entry point — every user can swap freely regardless of their individual allowlist status, rendering the extension's per-user gate completely non-functional.

## Finding Description
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

`ExtensionCalling._beforeSwap` forwards that same `sender` value directly to the extension without modification: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, i.e., `allowedSwapper[pool][router]`. The end user's identity is permanently lost at the pool boundary — `msg.sender` inside the extension is the pool; `sender` is the router: [3](#0-2) 

In `exactInputSingle`, the router calls `pool.swap` directly with no user-identity forwarding: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. No existing guard in the extension, pool, or router recovers or forwards the originating user address.

## Impact Explanation
The `SwapAllowlistExtension` is documented to "Gate `swap` by swapper address, per pool." When the router is allowlisted, this invariant is broken: the extension decision (`allowedSwapper[pool][router] = true`) authorizes every user who calls through the router, regardless of whether they are individually allowlisted. Any pool deploying this extension to restrict swap access to a curated set of users has its access control completely bypassed by any unprivileged trader routing through `MetricOmmSimpleRouter`. This is a broken core pool functionality / admin-boundary break by an unprivileged path, meeting the contest's allowed impact gate.

## Likelihood Explanation
The router is the primary intended swap interface for end users. Pool admins who deploy `SwapAllowlistExtension` and want to allow router-based swaps will allowlist the router — this is the natural and expected operational pattern. No special attacker capability is required beyond calling `exactInputSingle` on the router. The bypass is reachable in any realistic deployment of this extension with the router allowlisted, is repeatable, and requires no privileged access.

## Recommendation
`SwapAllowlistExtension.beforeSwap` must check the end user, not the immediate caller. Preferred options:

1. **Pass the end user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. Requires router cooperation.
2. **Add a dedicated `originator` forwarding field** to the extension interface so the router can attest the originating user, and the extension validates it.
3. **Require users to call the pool directly** when the allowlist extension is active, and document this constraint clearly, removing the router from the allowlist.

## Proof of Concept
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
