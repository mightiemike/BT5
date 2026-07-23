### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass via Router — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. If the router is allowlisted (the only way to enable router-mediated swaps), every non-allowlisted user can bypass the per-user gate by routing through the router.

---

### Finding Description

`SwapAllowlistExtension` is documented as gating `swap` by swapper address, per pool. The admin registers individual addresses via `setAllowedToSwap(pool, swapper, true)`.

The call chain when a user swaps through the router is:

```
user → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   [msg.sender = router]
              → _beforeSwap(msg.sender=router, recipient, ...)
                   → extension.beforeSwap(sender=router, ...)
``` [1](#0-0) 

The pool passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [2](#0-1) 

The extension then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct for pool-namespacing) and `sender` is the **router**, not the actual user. The allowlist lookup is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | All router-mediated swaps fail, even for individually allowlisted users |
| Allowlist the router | Every user on earth can bypass the per-user gate by routing through the router |

Neither option achieves the intended per-user gating. Note that `DepositAllowlistExtension.beforeAddLiquidity` does **not** share this bug — it correctly ignores `sender` and checks `owner` (the position owner), which the pool passes as a distinct parameter. [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` for KYC/compliance or counterparty-restriction purposes provides zero protection against any user who routes through `MetricOmmSimpleRouter`. The attacker does not need any special privilege — they simply call the public router. This is a direct bypass of a configured access-control guard with fund-impacting consequences: non-allowlisted users can execute swaps that drain pool liquidity or extract value from a curated pool that was intended to be restricted.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool admin who deploys `SwapAllowlistExtension` and also wants users to be able to use the router (the normal production path) will be forced to allowlist the router, immediately opening the bypass to all users. The trigger requires no special timing, no privileged role, and no unusual token behavior — any public user can exploit it on every swap.

---

### Recommendation

The extension must check the actual end-user address, not the intermediate router. Two viable approaches:

1. **Extension-data forwarding**: Have `MetricOmmSimpleRouter` encode `msg.sender` (the actual user) into `extensionData` before forwarding to the pool. The extension decodes and checks that address instead of `sender`.

2. **Dedicated sender parameter**: Add a separate `originalSender` field to the `beforeSwap` hook interface that the pool populates from a trusted periphery-supplied value, keeping `sender` as the immediate caller for callback-settlement purposes.

The `DepositAllowlistExtension` pattern (checking `owner`, a parameter distinct from `sender`) is the correct model to follow.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Admin calls `setAllowedToSwap(pool, router, true)` — required to let Alice use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → Bob's swap succeeds.
7. Bob has bypassed the per-user allowlist and executed a swap on a curated pool. [5](#0-4) [6](#0-5)

### Citations

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
