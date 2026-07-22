Let me trace the relevant code paths to evaluate this claim.

Now I have all the information needed. Let me trace the exact call chain precisely.

The call chain is fully confirmed. Here is the assessment:

---

### Title
SwapAllowlistExtension Receives Router Address as `sender` Instead of Original EOA, Allowing Any Caller to Bypass Per-EOA Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against its per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `swap()` receives `msg.sender = router`, passes that as `sender` to `_beforeSwap`, and the extension therefore checks whether the **router contract** is allowlisted — not the original EOA. A pool admin who wants to allow specific EOAs to swap through the router must allowlist the router address itself, which then grants unrestricted swap access to every caller of the router.

### Finding Description

**Step-by-step call chain:**

1. EOA calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(...)`. At this point `msg.sender` seen by the pool is the **router address**. [1](#0-0) 
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, forwarding the **router address** as `sender`. [2](#0-1) 
4. `ExtensionCalling._beforeSwap` ABI-encodes `sender` (= router) and dispatches to every configured extension. [3](#0-2) 
5. `SwapAllowlistExtension.beforeSwap` evaluates:
   ```solidity
   allowAllSwappers[msg.sender] || allowedSwapper[msg.sender][sender]
   // msg.sender = pool address, sender = router address
   ```
   The original EOA is never consulted. [4](#0-3) 

**The two broken states this creates:**

| Pool admin configuration | Effect |
|---|---|
| Router NOT allowlisted | All router swaps revert with `NotAllowedToSwap`, even for EOAs that are individually allowlisted — breaking legitimate user flows |
| Router IS allowlisted | Every EOA in the world can swap through the router regardless of individual allowlist status — the per-EOA gate is fully bypassed |

There is no configuration that achieves "only allowlisted EOAs may swap through the router."

### Impact Explanation

The `SwapAllowlistExtension` is a core access-control primitive for restricting which addresses may trade in a pool. When the router is used, the extension's per-EOA gating is structurally inoperable: either all router callers are blocked or all router callers are permitted. Any pool that relies on this extension to restrict swap access to a curated set of addresses can be bypassed by any unprivileged EOA simply by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). This constitutes broken core pool functionality and an admin-boundary break reachable through a public, permissionless path.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap interface. Any user who discovers that a pool uses `SwapAllowlistExtension` can trivially route through the router to bypass the allowlist. No special privileges, flash loans, or unusual token behavior are required.

### Recommendation

The pool must propagate the original caller's identity through the router. One standard approach is for the router to ABI-encode the original `msg.sender` into `extensionData` and for the extension to decode and verify it — but this requires the extension to trust the router, which introduces its own complexity. A cleaner solution is for the pool's `swap()` signature to accept an explicit `swapper` parameter (separate from `msg.sender`) that the router fills with its own `msg.sender`, and for the extension to check that field. Alternatively, the `SwapAllowlistExtension` documentation must explicitly state that it gates the **direct caller of the pool** (i.e., the router), and pool admins must allowlist routers rather than EOAs when router access is intended.

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_routerBypassesSwapAllowlist() public {
    // Pool configured with SwapAllowlistExtension
    // EOA `alice` is NOT on the allowlist
    // Router IS on the allowlist (admin had to do this to let any EOA use the router)

    address alice = makeAddr("alice");
    // alice is not allowlisted
    assertFalse(swapExtension.isAllowedToSwap(address(pool), alice));
    // router IS allowlisted
    assertTrue(swapExtension.isAllowedToSwap(address(pool), address(router)));

    // alice swaps through the router — succeeds despite not being allowlisted
    vm.prank(alice);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: alice,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    }));
    // alice received tokens — allowlist bypassed
}
```

The extension receives `sender = address(router)` (allowlisted), so the check passes for `alice` even though `allowedSwapper[pool][alice]` is `false`.

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
