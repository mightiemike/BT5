The code path is fully traceable. Let me confirm the exact `sender` binding at each hop.

The call chain is fully confirmed. Here is the determination:

---

### Title
Router-Mediated Swap Bypasses `SwapAllowlistExtension` Per-User Gate — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap` call. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the end user. Any user who calls through an allowlisted router address bypasses the per-user allowlist entirely.

### Finding Description

The call chain is:

```
attacker → MetricOmmSimpleRouter.exactInputSingle (msg.sender = attacker)
         → pool.swap(...)                          (msg.sender to pool = router)
         → _beforeSwap(msg.sender = router, ...)
         → SwapAllowlistExtension.beforeSwap(sender = router, ...)
         → checks allowedSwapper[pool][router]     ← router is allowlisted, passes
```

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap` directly with no forwarding of the original caller: [1](#0-0) 

The pool passes its own `msg.sender` (the router) as `sender` to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that router address as the `sender` argument to the extension: [3](#0-2) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, i.e., `allowedSwapper[pool][router]`: [4](#0-3) 

If the pool admin has allowlisted the router address (a natural action to permit router-mediated swaps for their curated users), the check passes for **every caller** of the router, regardless of whether the actual end user is in `allowedSwapper[pool][attacker]`.

### Impact Explanation
Any user can execute swaps on a curated pool that is intended to be restricted to an explicit allowlist, simply by routing through `MetricOmmSimpleRouter`. The pool's curation invariant — that only explicitly allowlisted end-users may trade — is broken. Disallowed users can drain LP value from a pool whose admin believed it was protected.

### Likelihood Explanation
The pool admin must have allowlisted the router address. This is a natural and expected configuration: a pool admin who wants to allow router-mediated swaps for their allowlisted users would add the router to `allowedSwapper`. The admin has no way to simultaneously allow router-mediated swaps AND enforce per-user identity checks, because the extension only sees the router as `sender`. The vulnerability is structural, not dependent on a misconfiguration that a careful admin would avoid.

### Recommendation
`SwapAllowlistExtension.beforeSwap` should check the `recipient` parameter (the economic beneficiary) rather than — or in addition to — `sender`, or the router should forward the original caller's address through `extensionData` so the extension can gate on the true end user. Alternatively, the extension documentation must explicitly warn that allowlisting any intermediary contract (router, multicall) opens the pool to all callers of that contract.

### Proof of Concept

```solidity
// Pool admin setup:
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// attacker is NOT in allowedSwapper[pool][attacker]

// Attacker action:
vm.prank(attacker);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
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
// Swap succeeds: allowedSwapper[pool][router] == true, attacker bypasses the gate
assertFalse(swapExtension.isAllowedToSwap(address(pool), attacker)); // attacker never allowlisted
// but swap completed — invariant violated
```

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
