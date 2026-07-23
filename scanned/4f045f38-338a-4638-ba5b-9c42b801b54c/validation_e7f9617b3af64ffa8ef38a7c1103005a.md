The call path is fully traceable through the scoped code. Let me lay out the exact chain:

The call chain is fully confirmed. Here is the complete trace and verdict:

**Exact call chain:**

1. Un-allowlisted EOA → `MetricOmmSimpleRouter.exactInputSingle(...)` [1](#0-0) 
2. Router calls `pool.swap(...)` — at this point `msg.sender` inside the pool is the **router address**, not the original EOA [2](#0-1) 
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` — the router address is forwarded as `sender` [3](#0-2) 
4. `SwapAllowlistExtension.beforeSwap` receives `sender = router_address` and checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]` [4](#0-3) 

**The corrupted invariant:** The extension is documented as "Gates `swap` by swapper address, per pool." [5](#0-4)  The `sender` it receives is not the swapper (original EOA) but the router. If the pool admin allowlists the router address — a natural and expected action for a public router — the per-user gate collapses entirely: every user who routes through the router passes the check regardless of their individual allowlist status.

---

### Title
`SwapAllowlistExtension` checks router address as swapper identity, allowing any EOA to bypass per-user swap gate via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` = `msg.sender` of `MetricOmmPool.swap`. When a swap is routed through `MetricOmmSimpleRouter`, that value is the router contract address, not the originating EOA. Allowlisting the router (the natural configuration for a pool that wants to accept router-mediated swaps) grants every user unconditional swap access, defeating the per-user gate entirely.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check becomes `allowedSwapper[pool][router]`. If the pool admin has called `setAllowedToSwap(pool, router, true)` — the only way to permit any router-mediated swap — the condition is satisfied for **every** EOA that calls the router, regardless of whether that EOA is individually allowlisted.

The router stores the original EOA only in transient storage for its own callback payment logic (`_setNextCallbackContext`); it never surfaces it to the pool or extension.

### Impact Explanation
Any user can execute swaps on a pool whose admin intended to restrict access to a specific set of allowlisted addresses. The pool's token balances decrease for every such swap (output token is transferred to the caller's chosen recipient). The allowlist invariant — the sole access-control mechanism on the swap path for these pools — is rendered inoperative for all router-mediated swaps. This is broken core pool functionality causing direct, repeatable fund outflow from the pool to unauthorized parties.

### Likelihood Explanation
The scenario requires the pool admin to allowlist the router address. This is not a misconfiguration edge case — it is the **only** way to allow any user to swap via the router on an allowlisted pool. Any pool that is intended to be accessible through the public router while also restricting individual swappers will inevitably reach this state. The path is fully public (no privileged role needed by the attacker) once the router is allowlisted.

### Recommendation
The extension must gate on the original EOA, not the immediate `pool.swap` caller. Two viable approaches:

1. **Pass originator through `extensionData`**: The router encodes `msg.sender` (the original EOA) into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.
2. **Dedicated originator field in the hook interface**: Add an `originator` parameter to `beforeSwap` that the pool populates from a transient-storage slot set by the router before calling `pool.swap`, analogous to how Uniswap v4 handles `hookData`.

Option 1 is implementable without changing the core interface; option 2 is cleaner but requires a core interface change.

### Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension; allowAllSwappers[pool] = false.
2. Pool admin calls: extension.setAllowedToSwap(pool, address(router), true)
   (no individual EOA is allowlisted)
3. Un-allowlisted EOA calls:
   router.exactInputSingle(ExactInputSingleParams{ pool: pool, ... })
4. Router calls pool.swap(...) — msg.sender inside pool = router
5. Pool calls extension.beforeSwap(sender=router, ...)
6. Extension evaluates: allowedSwapper[pool][router] == true → passes
7. Swap executes; pool token balance decreases; un-allowlisted EOA receives output.
8. Assert: swap succeeded despite EOA never being individually allowlisted.
```

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-80)
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-10)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
