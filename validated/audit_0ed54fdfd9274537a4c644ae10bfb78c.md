All code paths are confirmed. The claim is accurate against the production code.

- `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap()` [1](#0-0) 
- `ExtensionCalling._beforeSwap()` encodes that `sender` and forwards it to the extension [2](#0-1) 
- `SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]` where `sender` is the direct pool caller [3](#0-2) 
- `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router `msg.sender` to the pool, with no end-user identity forwarded [4](#0-3) 
- `DepositAllowlistExtension.beforeAddLiquidity()` correctly checks `owner` (position beneficiary) rather than `sender` (direct caller), confirming the asymmetry [5](#0-4) 

---

Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which is always the direct caller of `pool.swap()`. When users interact through `MetricOmmSimpleRouter`, the router is the direct caller, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. Any pool admin who allowlists the router to enable legitimate router-mediated swaps simultaneously grants every non-allowlisted address the ability to bypass the curated pool's access control.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()` (L230–240). `ExtensionCalling._beforeSwap()` encodes this value and calls each configured extension (L160–176). `SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()` (L37).

`MetricOmmSimpleRouter.exactInputSingle()` calls `IMetricOmmPoolActions(params.pool).swap(...)` directly (L72–80). The actual end-user's address (`msg.sender` of the router call) is stored only in transient callback context for payment purposes and is never forwarded to the pool or extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

For a curated pool to be usable through the router, the admin must call `setAllowedToSwap(pool, address(router), true)`. Once the router is allowlisted, the check passes for every caller of the router, including non-allowlisted addresses. The allowlist is fully bypassed.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity()` checks the `owner` argument (the position beneficiary, L38), not `sender` (the direct caller), because `addLiquidity()` carries an explicit `owner` field. The `swap()` interface carries no equivalent caller-identity field beyond `msg.sender`, so there is no correct field for `SwapAllowlistExtension` to check when the router is the intermediary.

## Impact Explanation
An unprivileged, non-allowlisted trader can execute swaps against a curated pool's LP reserves under conditions the LP depositors never consented to. Curated pools restrict access to prevent unauthorized extraction of LP value, front-running by untrusted counterparties, or regulatory non-compliance. A successful bypass constitutes a direct admin-boundary break: the pool admin's configured access control is circumvented by an unprivileged path, with direct potential for LP fund impact through unauthorized swap execution against restricted liquidity.

## Likelihood Explanation
The bypass requires the pool admin to have allowlisted the router address. This is the natural and expected production configuration for any curated pool whose legitimate users are expected to interact through the standard periphery (`MetricOmmSimpleRouter`). A pool admin who forces direct EOA calls (never allowlisting the router) avoids the issue but makes the router entirely unusable for that pool. The misconfiguration is the expected production path for router-accessible curated pools, making likelihood medium.

## Recommendation
1. **Short-term**: Document explicitly that `SwapAllowlistExtension` cannot correctly gate router-mediated swaps. Pool admins must never allowlist the router address; instead, they must require direct pool calls from allowlisted EOAs only.
2. **Long-term**: Redesign the swap allowlist to check an identity that survives router indirection. Options include:
   - Requiring the router to embed the actual user's address in `extensionData` and verifying it with a signature inside the extension.
   - Adding an explicit `swapper` field to the pool's `swap()` interface (analogous to `owner` in `addLiquidity()`), so the extension can check the intended beneficiary rather than the direct caller.

## Proof of Concept
```
1. Pool admin deploys a curated pool with SwapAllowlistExtension configured.
2. Pool admin allowlists the router:
       swapExtension.setAllowedToSwap(pool, address(router), true);
3. Non-allowlisted attacker (address X, not in allowedSwapper) calls:
       router.exactInputSingle({pool: pool, recipient: X, ...});
4. Router calls pool.swap() — msg.sender to the pool is the router address.
5. Pool calls _beforeSwap(router, ...) → ExtensionCalling encodes sender=router
   → SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
6. Swap executes. Attacker receives output tokens from the curated pool's LP reserves.
   The allowlist never evaluated address X.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

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
