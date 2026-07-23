### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass Per-User Swap Allowlist via Router - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the actual user. If the pool admin allowlists the router (the natural step to let users trade through the supported periphery), every unpermissioned user can bypass the per-user allowlist by calling the router.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension`:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

**What the pool passes as `sender`:**

`MetricOmmPool.swap()` passes `msg.sender` (the direct caller of the pool) as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

**What the router passes to the pool:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router itself `msg.sender` of that call:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

**The broken invariant:**

The full call chain is:

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap()          [msg.sender = router]
     → _beforeSwap(sender = router, ...)
     → SwapAllowlistExtension.beforeSwap(sender = router)
     → checks allowedSwapper[pool][router]   ← actual user is never checked
```

The extension checks whether the **router** is on the allowlist, not whether the **user** is. A pool admin who wants to allow users to trade through the supported periphery must allowlist the router. Once the router is allowlisted, `allowedSwapper[pool][router] == true` passes for every caller of the router, regardless of who they are.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific addresses is completely bypassed. Any unpermissioned user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) targeting the pool. The extension sees `sender = router`, which is allowlisted, and the swap executes. The unauthorized user trades against the pool's liquidity, extracting value or disrupting the pool's intended access model. This is a direct loss of curation policy and potentially of LP funds if the pool was designed to trade only with trusted counterparties.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap interface for end users. Any pool admin who deploys a curated pool with `SwapAllowlistExtension` and also wants users to use the router faces a forced choice: either allowlist the router (breaking the allowlist for everyone) or do not allowlist the router (making the allowlist incompatible with the router). The bypass is reachable by any user with no special privileges, no malicious setup, and no non-standard tokens — just a standard router call.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the economic actor (the end user), not the intermediary contract. Two approaches:

1. **Pass the original user through the router:** Modify `MetricOmmSimpleRouter` to forward `msg.sender` (the user) as part of `extensionData`, and update `SwapAllowlistExtension` to decode and check that value. This requires a convention between router and extension.

2. **Check `recipient` instead of `sender` for swap allowlisting:** If the pool's intent is to gate who receives output, `recipient` is the correct field. If the intent is to gate who initiates the swap, the pool must propagate the original user's address through the call stack rather than using `msg.sender` of `pool.swap()`.

The cleanest fix is for the pool's `swap` function to accept an explicit `payer` or `initiator` address (analogous to how `addLiquidity` separates `msg.sender` payer from `owner`), and for the router to pass `msg.sender` (the user) in that field. The extension would then check that field.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        recipient: attacker,
        zeroForOne: true,
        amountIn: X,
        ...
    })
  - Router calls pool.swap(attacker, true, X, ...)
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - Swap executes; attacker receives output tokens

Result:
  - attacker successfully swapped against the curated pool
  - SwapAllowlistExtension never checked attacker's address
  - allowedSwapper[pool][attacker] == false was never consulted
``` [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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
