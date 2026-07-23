### Title
`SwapAllowlistExtension.beforeSwap` checks the router address as `sender` instead of the end user, allowing any unprivileged caller to bypass the configured swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is a production periphery extension that pool admins configure to gate which addresses may call `swap()`. Its `beforeSwap` hook receives `sender` — the `msg.sender` of the pool's `swap()` call — and checks it against the per-pool allowlist. When `MetricOmmSimpleRouter` is the intermediary, `sender` is the router contract, not the end user. A pool admin who allowlists the router to enable router-based swaps inadvertently grants every user of that router unrestricted swap access, completely defeating the allowlist.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to every configured extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol  L230-L240
_beforeSwap(
    msg.sender,   // ← sender = immediate pool caller
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

`SwapAllowlistExtension.beforeSwap` then checks that exact value:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  L31-L41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` of that call:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  L71-L80
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

The end user's address (`msg.sender` of `exactInputSingle`) is stored only in transient callback context for payment settlement — it is never forwarded to the pool or to any extension. The extension therefore sees `sender = MetricOmmSimpleRouter`, not the actual trader.

Contrast this with `DepositAllowlistExtension`, which correctly checks `owner` (the economic beneficiary of the position) rather than `sender` (the immediate caller):

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol  L32-L42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The asymmetry is the root cause: for liquidity the allowlist gates the economic beneficiary; for swaps it gates the immediate caller, which is the router when the periphery is used.

---

### Impact Explanation

A pool admin who deploys a pool with `SwapAllowlistExtension` in `BEFORE_SWAP_ORDER` intends to restrict swap access to a curated set of addresses (e.g., KYC'd traders, institutional counterparties, or specific integrators). To allow those users to swap through `MetricOmmSimpleRouter`, the admin must call `setAllowedToSwap(pool, router, true)`. The moment the router is allowlisted, every user of the router — including addresses the admin explicitly never allowlisted — can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` and the `beforeSwap` check passes unconditionally. The configured guard is silently bypassed for the entire user population of the router. LPs who deposited under the assumption that only vetted counterparties would trade against them are exposed to the full public.

---

### Likelihood Explanation

The scenario is the natural operational path: a pool admin configures a swap allowlist for access control, then allowlists the canonical router so that permitted users can trade through the standard UI. There is no warning in the extension, the interface, or the factory that doing so opens the pool to all router users. The mismatch between intent and effect is invisible at configuration time and requires no attacker sophistication — any user who calls the router with a valid pool address bypasses the guard.

---

### Recommendation

The `beforeSwap` hook should gate on the end user, not the immediate pool caller. Two complementary fixes:

1. **Pass the originating user through `extensionData`**: `MetricOmmSimpleRouter` should encode `msg.sender` (the end user) into `extensionData` for each hop. `SwapAllowlistExtension` would decode and check that address when `sender` is a known router.

2. **Alternatively, check `sender` AND `recipient` or require direct-call-only**: document explicitly that the extension is incompatible with router intermediaries, and add a factory-level flag or NatSpec warning so pool admins cannot silently misconfigure it.

---

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow legitimate users to trade via `MetricOmmSimpleRouter`.
3. Attacker (address never added to the allowlist) calls:
   ```solidity
   router.exactInputSingle(ExactInputSingleParams({
       pool: pool,
       tokenIn: token0,
       ...
       extensionData: ""
   }));
   ```
4. Pool calls `_beforeSwap(msg.sender=router, ...)`.
5. `SwapAllowlistExtension.beforeSwap` receives `sender = router`.
6. `allowedSwapper[pool][router] == true` → check passes.
7. Attacker's swap executes against pool liquidity despite never being individually allowlisted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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
