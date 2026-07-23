### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router address**, not the actual user. The extension therefore checks the router's allowlist status. If the pool admin allowlists the router to enable router-based swaps, every user — including explicitly disallowed ones — can bypass the curated allowlist by routing through the router.

---

### Finding Description

**Trace through the call stack:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — at this point `msg.sender` inside the pool is the **router address**.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, forwarding the router address as `sender`.
4. `ExtensionCalling._beforeSwap` encodes and dispatches to `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
5. `SwapAllowlistExtension.beforeSwap` evaluates:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool (correct), `sender` = **router** (wrong — should be the end user).

The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**Contrast with `DepositAllowlistExtension`:** The deposit extension correctly checks `owner` (the position owner explicitly passed through the call chain), not `sender` (the caller). The swap extension has no equivalent "economically relevant actor" parameter — it only receives `sender` which collapses to the router when the router is the intermediary.

**The two failure modes:**

| Scenario | Outcome |
|---|---|
| Router is NOT allowlisted | All allowlisted users are blocked from using the router; they must call the pool directly |
| Router IS allowlisted | Every user (including disallowed ones) can bypass the allowlist by routing through the router |

A pool admin who wants to support router-based swaps on a curated pool is forced into the second scenario, which nullifies the allowlist entirely.

---

### Impact Explanation

**Direct loss of curation policy / unauthorized fund flows.** A curated pool (e.g., KYC-gated, institution-only) that allowlists the router to support normal UX becomes open to any address. Disallowed users can execute swaps, draining LP value at oracle-anchored prices that the pool admin intended to restrict to vetted counterparties. This is a direct bypass of a configured access-control guard with fund-impacting consequences (unauthorized swaps against LP positions).

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool admin deploying a `SwapAllowlistExtension` and expecting users to interact via the router will encounter this. The bypass requires no special privileges — any user with a standard EOA can call `exactInputSingle` on the router. The only precondition is that the pool admin has allowlisted the router (a natural and expected configuration step).

---

### Recommendation

Pass the **original initiator** through the swap path rather than `msg.sender` of the pool call. Two approaches:

1. **Preferred — router forwards `msg.sender` as `sender` in `extensionData`**: The pool or extension reads the true initiator from a signed/authenticated field in `extensionData`. This requires a protocol-level convention.

2. **Simpler — align with the deposit pattern**: Add a `swapper` parameter to `swap()` (analogous to `owner` in `addLiquidity`) that the router fills with `msg.sender` before calling the pool. The pool passes this explicit `swapper` to `_beforeSwap` instead of its own `msg.sender`. The pool enforces that `swapper == msg.sender` for direct calls (no router), preserving the direct-call invariant.

The `DepositAllowlistExtension` already demonstrates the correct pattern: it checks `owner` (the explicit position owner), not `sender` (the caller). The swap allowlist should adopt the same separation.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists userA: setAllowedToSwap(pool, userA, true)
  - Pool admin allowlists router: setAllowedToSwap(pool, router, true)
    (required so that userA can use the router)

Attack:
  - userB (NOT allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap() → msg.sender in pool = router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true → passes
  - userB's swap executes on the curated pool

Result:
  - userB bypasses the swap allowlist entirely
  - Any number of disallowed users can repeat this
```

**Key code references:**

`MetricOmmPool.swap` passes `msg.sender` (router) as `sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` = router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` as `msg.sender` = router: [4](#0-3) 

`DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (not `sender`), demonstrating the intended pattern: [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
