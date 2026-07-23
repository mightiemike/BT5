### Title
SwapAllowlistExtension Checks Router Address as Swapper Identity, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is always `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the actual user. A pool admin who allowlists the router address to enable router-based swaps for their curated pool inadvertently opens the pool to every user, completely defeating the allowlist.

---

### Finding Description

**Actor binding in the pool's `swap` function:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

**What the extension checks:**

`SwapAllowlistExtension.beforeSwap` receives that value as `sender` and checks it against the per-pool allowlist:

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [2](#0-1) 

Here `msg.sender` is the pool (correct), and `sender` is whoever called `pool.swap()`.

**What the router passes:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly from the router contract. The router stores the real user in transient storage for the payment callback, but never forwards the user's identity to the pool:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

Because the router is `msg.sender` of the `pool.swap()` call, the extension receives `sender = router_address`. The check therefore evaluates `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][actual_user]`.

The same substitution occurs in `exactInput` (multi-hop) and `exactOutputSingle`/`exactOutput`, because in every case the router contract is the direct caller of `pool.swap()`. [4](#0-3) 

**The bypass path:**

A pool admin who wants allowlisted users to be able to use the router must allowlist the router address:

```
setAllowedToSwap(pool, router_address, true)
```

Once the router is allowlisted, `allowedSwapper[pool][router_address] == true`, so `beforeSwap` passes for every caller regardless of their individual allowlist status. Any non-allowlisted user can call `router.exactInputSingle(...)` and the guard silently approves the swap.

Contrast this with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly checks the `owner` argument (the position beneficiary), not `sender` (the payer/operator). The swap extension has no equivalent separation and no mechanism to recover the real user's address from the router call. [5](#0-4) 

---

### Impact Explanation

The `SwapAllowlistExtension` is the production mechanism for curated pools (KYC pools, institutional pools, restricted-access pools). Once the router is allowlisted — a necessary step for any pool that wants to support the standard periphery — the allowlist provides zero protection. Any address can execute swaps against the pool by routing through `MetricOmmSimpleRouter`. This constitutes a complete admin-boundary break: the pool admin's access-control configuration is bypassed by an unprivileged path (the public router). Depending on the pool's purpose, this can result in direct loss of LP assets (e.g., a pool priced for a restricted counterparty set is now accessible to arbitrageurs) or regulatory/compliance failure with fund-impacting consequences.

---

### Likelihood Explanation

The trigger is a pool admin allowlisting the router — a routine, non-malicious action required to make the pool usable through the standard periphery. The bypass requires no special privileges, no flash loans, and no exotic token behavior: any EOA calls `exactInputSingle` on the router. The combination of a natural admin action and a trivially reachable exploit path makes this medium-to-high likelihood in any production deployment that uses both `SwapAllowlistExtension` and `MetricOmmSimpleRouter`.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the actual end-user identity, not the immediate caller of `pool.swap()`. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`. The extension decodes and verifies it. This requires a trusted encoding convention and the extension to verify the pool is the caller (already enforced by `onlyPool` in `BaseMetricExtension`).

2. **Check `recipient` instead of `sender`**: For single-hop swaps where the user is also the recipient, the extension could check `recipient`. This breaks for multi-hop paths where intermediate recipients are the router itself.

3. **Separate router-aware allowlist**: Introduce a router registry so the extension can unwrap the real payer from transient context when `sender` is a known router. This is the most robust but most invasive change.

The simplest safe fix is option 1: require the router to attest the real user in `extensionData` and have the extension verify that attestation when `sender` is a known intermediary.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted
swapExtension.setAllowedToSwap(address(pool), alice, true);
// Admin also allowlists the router so alice can use it
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attack: bob (not allowlisted) routes through the router
vm.prank(bob);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:            address(pool),
    tokenIn:         address(token0),
    tokenOut:        address(token1),
    zeroForOne:      true,
    amountIn:        1_000,
    amountOutMinimum: 0,
    recipient:       bob,
    deadline:        block.timestamp + 1,
    priceLimitX64:   0,
    extensionData:   ""
}));
// ✓ swap succeeds — bob bypassed the allowlist
// Extension checked allowedSwapper[pool][router] == true, never checked bob
```

The pool's `_beforeSwap` receives `sender = address(router)`. [6](#0-5) 

The extension evaluates `allowedSwapper[pool][router] == true` and returns the success selector, allowing the swap to settle against LP assets that were intended to be accessible only to `alice`. [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
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
