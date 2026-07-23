Looking at the swap path through `MetricOmmSimpleRouter` → `MetricOmmPool.swap` → `ExtensionCalling._beforeSwap` → `SwapAllowlistExtension.beforeSwap`, there is a concrete wrong-actor binding.

**Trace:**

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to the extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap`, the pool's `msg.sender` is the **router**, not the end user: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. The actual user's address is never consulted.

---

### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper on every `MetricOmmSimpleRouter`-mediated swap, enabling full allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` receives `sender = msg.sender` of the pool's `swap` call. When any swap is routed through `MetricOmmSimpleRouter`, that value is the router's address. The extension therefore checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. If the router is allowlisted for the pool — a natural admin action for a trusted periphery contract — every unpermissioned user can bypass the curation gate by routing through the router.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` encodes that value and forwards it to every configured extension. `SwapAllowlistExtension.beforeSwap` then performs:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct), but `sender` is the router (wrong). The actual end-user address is never visible to the extension. The pool's `swap` interface provides no mechanism for the router to inject the real originator.

This affects all four router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) and also the intermediate hops inside `_exactOutputIterateCallback`, where the router calls `pool.swap` from within the callback — again with `msg.sender` = router. [5](#0-4) 

### Impact Explanation
Two concrete failure modes, both fund-impacting:

**Bypass (high impact):** A pool admin allowlists the router as a trusted periphery contract (`setAllowedToSwap(pool, router, true)`). Any non-allowlisted user calls `router.exactInputSingle(...)`. The extension sees `sender = router`, finds it allowlisted, and permits the swap. The curation gate is fully defeated; the pool accepts trades from arbitrary counterparties the admin intended to exclude.

**Lockout (medium impact):** The admin does not allowlist the router (only individual users). Allowlisted users who call the router are blocked because the extension sees `sender = router` and finds it not allowlisted. The router — the protocol's primary swap interface — is unusable for any allowlisted pool, breaking core swap functionality.

### Likelihood Explanation
Allowlisting the router is a natural and expected admin action: the router is the protocol's own periphery contract, and admins who want to permit "trusted" access paths will add it. The bypass is therefore reachable through a normal, non-malicious admin configuration. The lockout is automatic and requires no admin mistake at all — it triggers the moment any allowlisted user attempts to use the router.

### Recommendation
The pool's `swap` function should accept an explicit `originator` parameter that the router sets to `msg.sender` (the actual user) before calling the pool. The extension would then gate on `originator` rather than the pool's `msg.sender`. Alternatively, the router can encode the real user in `extensionData` and the extension can decode it, though this requires coordinated changes to both contracts. At minimum, the `SwapAllowlistExtension` documentation must state that it is incompatible with router-mediated swaps and that allowlisting the router opens the pool to all users.

### Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension configured.
2. Admin calls extension.setAllowedToSwap(pool, alice, true)   // alice is the intended grantee
3. Admin calls extension.setAllowedToSwap(pool, router, true)  // router added as trusted periphery
4. Attacker (bob, not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool: pool, tokenIn: ..., tokenOut: ..., recipient: bob, ...
       })
5. pool.swap is called with msg.sender = router.
6. SwapAllowlistExtension.beforeSwap receives sender = router.
7. allowedSwapper[pool][router] == true  →  check passes.
8. Bob's swap executes; allowlist is bypassed.
```

The root cause is in `MetricOmmPool.sol` L231 (`msg.sender` passed as `sender`) combined with `SwapAllowlistExtension.sol` L37 (gates on that `sender` value), with the router at `MetricOmmSimpleRouter.sol` L72-80 as the unprivileged trigger path. [6](#0-5) [7](#0-6) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
