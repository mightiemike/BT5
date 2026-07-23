Now I have all the information needed. Let me trace the exact call path for the swap allowlist bypass.

### Title
`SwapAllowlistExtension` gates on the router address instead of the actual user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual user. If the pool admin allowlists the router (the only way to enable router-mediated swaps on a curated pool), every user — including those explicitly excluded — can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender` value: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is `msg.sender` to the pool: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The pool admin faces an impossible choice:

- **Do not allowlist the router** → legitimate allowlisted users cannot use the router at all (broken core functionality).
- **Allowlist the router** → every user, including those explicitly excluded, can bypass the allowlist by routing through the router.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores the `sender` (first argument) and gates on `owner` (second argument), which is the position owner regardless of who calls the pool: [5](#0-4) 

The swap extension has no equivalent "owner" concept — it only receives `sender` (the immediate caller), making it structurally incompatible with router-mediated flows.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted integrators) can be fully bypassed by any user routing through `MetricOmmSimpleRouter`. The unauthorized user executes real swaps against the pool's liquidity, receiving output tokens and paying input tokens, with no revert. This is a direct curation failure: the pool's access-control invariant is broken for every router-mediated swap.

---

### Likelihood Explanation

The router is the primary user-facing interface for the protocol. Any pool admin who deploys a curated pool and wants users to interact via the router must allowlist the router address. This is a natural, expected configuration step — not an exotic edge case. Once the router is allowlisted, the bypass is trivially reachable by any address calling `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router.

---

### Recommendation

The `beforeSwap` hook must gate on the **economic actor** (the end user), not the immediate caller. Two complementary fixes:

1. **Pass the original user through the router**: The router should encode the original `msg.sender` into `extensionData` and the extension should decode and check it. This requires a coordinated change to the router and extension.

2. **Mirror the deposit extension pattern**: Introduce an explicit `swapper` identity field in the swap call (analogous to `owner` in `addLiquidity`) so the pool can forward the true user address to extensions regardless of who calls `swap()`.

A minimal stopgap is to document that `SwapAllowlistExtension` is incompatible with router-mediated flows and must only be used on pools where all swaps are expected to arrive via direct pool calls.

---

### Proof of Concept

```
Setup:
  - Pool P has SwapAllowlistExtension active on beforeSwap.
  - Pool admin allowlists only address Alice: allowedSwapper[P][Alice] = true.
  - Pool admin also allowlists the router so Alice can use it: allowedSwapper[P][router] = true.

Attack (Bob, not allowlisted):
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...}).
  2. Router calls P.swap(recipient=Bob, ...) — msg.sender to pool = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[P][router] → true.
  5. Swap executes. Bob receives output tokens. No revert.

Result:
  Bob, who is not in the allowlist, successfully trades on the curated pool.
  The allowlist invariant is broken for every router-mediated swap.
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
