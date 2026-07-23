All four code paths are confirmed in the repository. The claim is fully supported:

1. `MetricOmmPool.swap()` passes `msg.sender` (the direct caller) as `sender` to `_beforeSwap`. [1](#0-0) 

2. `ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension via `_callExtensionsInOrder`. [2](#0-1) 

3. `SwapAllowlistExtension.beforeSwap` gates on `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()` — i.e., the router, not the end user. [3](#0-2) 

4. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` to the pool. [4](#0-3) 

The bypass is structurally unavoidable: for any allowlisted user to use the router, the admin must allowlist the router address, which then opens the pool to all router callers.

---

Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` mediates a swap, `msg.sender` to the pool is the router contract, not the end user. If the pool admin allowlists the router — the only way to let any allowlisted user use the standard periphery — every unpermissioned user can bypass the allowlist by routing through the router.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap` (lines 230–240). `ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension via `_callExtensionsInOrder` (lines 149–177). `SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()` (line 37). When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()`, the pool's `msg.sender` is the router contract (lines 72–80), not the end user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. For any allowlisted user to use the router, the pool admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and the check passes for every caller of the router — including users who were never individually allowlisted. No existing guard in the extension, pool, or router prevents this.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties is fully bypassed. Any unpermissioned address calls `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool. The extension sees `sender = router`, which is allowlisted, and permits the swap. The unauthorized user receives pool output tokens and the pool's LP position is exposed to trades from actors the admin explicitly excluded. This constitutes a direct admin-boundary break and potential LP fund loss on pools designed for restricted access (e.g., institutional or KYC-gated pools), meeting the Sherlock Medium threshold.

## Likelihood Explanation
The router is the standard, documented periphery path for end users. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address — there is no other mechanism. The moment the router is allowlisted, the bypass is live for all users. The trigger requires no special privilege: any EOA calls a public router function. Likelihood is Medium (requires the admin to have allowlisted the router, which is the natural operational step for any production curated pool).

## Recommendation
The extension must check the original user, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router**: `MetricOmmSimpleRouter` should forward `msg.sender` as an authenticated field in `extensionData` (verified via transient storage), and `SwapAllowlistExtension` should decode and check that field when `sender` is a known router.

2. **Preferred — add an explicit `originator` field**: Redesign the `beforeSwap` hook signature to include an `originator` address set by the pool to the original `msg.sender` of the top-level call, separate from the intermediary `sender`. The extension then gates on `originator`.

Until fixed, pool admins must not allowlist the router address and must require all allowlisted users to call `pool.swap()` directly.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack (bob, not allowlisted):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({
         pool: curated_pool,
         recipient: bob,
         zeroForOne: true,
         amountIn: X,
         ...
     })
  2. Router calls curated_pool.swap(bob, true, X, ...) with msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true ✓
  5. Swap executes; bob receives output tokens from the curated pool.

Result: bob, who was never allowlisted, successfully swaps against the curated pool.
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
