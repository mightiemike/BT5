Now I have enough context to analyze the full call chain. Let me read the pool's swap function and the router's swap call to confirm the sender identity passed to the extension.

### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the actual end-user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any pool admin who allowlists the router to enable router-mediated swaps for their approved users inadvertently opens the pool to **every** user who calls the router, completely defeating the per-user access gate.

---

### Finding Description

**Call chain producing the wrong identity:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         └─ pool.swap(recipient, ...)          // msg.sender = router
               └─ _beforeSwap(msg.sender, ...) // sender = router address
                     └─ SwapAllowlistExtension.beforeSwap(router, ...)
                           └─ allowedSwapper[pool][router]  ← wrong identity
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← router address when called through the router
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that value against the allowlist:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router — so the check is `allowedSwapper[pool][router]`.

**Contrast with `DepositAllowlistExtension`**, which correctly checks the `owner` parameter (the actual position owner, not the direct caller):

```solidity
// DepositAllowlistExtension.sol:38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

The deposit extension works correctly because the pool passes the explicit `owner` argument, which is the real depositor regardless of who calls `addLiquidity`. No equivalent "real swapper" parameter exists in the swap path — the pool only has `msg.sender`.

**The impossible configuration trap:**

| Pool admin choice | Effect |
|---|---|
| Do NOT allowlist the router | Allowlisted users cannot use the router at all (broken functionality) |
| Allowlist the router | Every user on Earth can bypass the per-user allowlist via the router |

There is no configuration that achieves "only allowlisted users may swap through the router."

---

### Impact Explanation

A pool deployer who configures `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers, or to prevent known MEV extractors) and then allowlists the router so those approved users can access the standard swap UX will unknowingly open the pool to **all** router callers. Any non-allowlisted address can call `router.exactInputSingle()` / `exactInput()` / `exactOutputSingle()` / `exactOutput()` and execute swaps against the pool. LP principal is directly at risk: the pool's oracle-priced liquidity is now accessible to actors the pool admin explicitly intended to exclude, enabling value extraction from LP positions that the allowlist was designed to prevent.

---

### Likelihood Explanation

The scenario is realistic and likely:

1. A pool admin deploys a pool with `SwapAllowlistExtension` to restrict swap access.
2. They allowlist specific user addresses.
3. They also allowlist the router (or the router's address is added to the allowlist as a convenience so approved users can use the standard periphery) — a natural operational step since the router is the canonical swap entry point.
4. Any unprivileged user calls the router and bypasses the gate.

No privileged escalation, no malicious setup, and no non-standard tokens are required. The attacker only needs to call a public router function.

---

### Recommendation

The `beforeSwap` hook receives both `sender` (direct pool caller) and `recipient`. Neither is the true end-user when the router intermediates. Two sound fixes:

1. **Pass the real user through `extensionData`**: The router already forwards `extensionData` to the pool. The pool admin can require callers to embed their address in `extensionData`, and the extension can decode and verify it. This is opt-in and requires router cooperation.

2. **Check `recipient` instead of `sender`** (if the pool's design intent is that the recipient is the economic beneficiary): This is only correct if `recipient == actual user`, which is not always true.

3. **Preferred — mirror the deposit pattern**: Add a `swapper` parameter to `pool.swap()` analogous to `owner` in `addLiquidity`, letting the caller declare the economic actor. The pool would then pass this declared address to the extension, and the extension would check it. The router would pass `msg.sender` as `swapper`.

Until fixed, pool admins must be warned that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)`.

---

### Proof of Concept

```solidity
// Setup:
// 1. Pool configured with SwapAllowlistExtension
// 2. Pool admin allowlists alice (approved user) and the router
//    extension.setAllowedToSwap(pool, alice, true);
//    extension.setAllowedToSwap(pool, address(router), true);  // needed for alice to use router
// 3. bob is NOT allowlisted

// Attack:
// bob calls the router directly — extension sees sender = router (allowlisted) → passes
vm.prank(bob);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 10_000,
    amountOutMinimum: 0,
    recipient: bob,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// ✓ swap succeeds — bob bypassed the allowlist
```

**Verification of the broken invariant:**
- `allowedSwapper[pool][bob]` = `false` (bob is not allowlisted)
- `allowedSwapper[pool][router]` = `true` (router is allowlisted so alice can use it)
- Extension check: `allowedSwapper[pool][router]` = `true` → no revert
- Bob swaps successfully against LP funds he was supposed to be blocked from accessing [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
