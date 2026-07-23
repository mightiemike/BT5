### Title
SwapAllowlistExtension gates the router address instead of the actual user, allowing any user to bypass the per-user swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` parameter, which is the pool's `msg.sender`. When users swap through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router (a natural action to enable router-based swaps), every user — including those not individually allowlisted — can bypass the per-user restriction by routing through the public router.

---

### Finding Description

**The mismatch.** `SwapAllowlistExtension.beforeSwap()` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension's caller). `sender` is whatever the pool passed as its own `msg.sender`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // <-- pool's msg.sender, i.e. whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

So the pool's `msg.sender` is the **router address**, not the originating user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**The asymmetry with the deposit allowlist.** `DepositAllowlistExtension.beforeAddLiquidity()` correctly checks `owner` — the position owner passed explicitly by the caller — rather than `sender`:

```solidity
// DepositAllowlistExtension.sol:32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The deposit guard gates the economically relevant actor (`owner`). The swap guard gates the direct pool caller (`sender` = router), which is the wrong identity when the periphery is in the call path.

**Two concrete failure modes:**

| Scenario | Admin action | Result |
|---|---|---|
| **Bypass** | Admin allowlists the router to enable router-based swaps | Every user, including non-allowlisted ones, can swap through the router |
| **DoS** | Admin allowlists individual users but not the router | Allowlisted users cannot use the router; they must call the pool directly |

The bypass scenario is the fund-impacting one: a pool intended to restrict swaps to a curated set of addresses is fully open to any user who routes through the public `MetricOmmSimpleRouter`.

---

### Impact Explanation

A pool deployer who configures `SwapAllowlistExtension` to gate swaps to specific counterparties (e.g., a KYC'd set, a whitelist of market makers, or a restricted liquidity program) loses that protection entirely if the router is allowlisted. Any unpermissioned user can execute swaps against the pool's liquidity, draining LP value at oracle-anchored prices without the intended access control. This is a direct loss of the protection the LP deposited under.

---

### Likelihood Explanation

Allowlisting the router is the natural action for a pool admin who wants to support the official periphery while restricting direct pool access. The admin has no reason to expect that allowlisting a trusted router contract opens the pool to all users. The `MetricOmmSimpleRouter` is a public, permissionless contract — any user can call it with any pool address and any recipient.

---

### Recommendation

The swap allowlist must gate the originating user, not the intermediary. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool. The extension decodes and checks it. This requires the extension to trust that the router is the pool's `msg.sender` (verifiable via `msg.sender == router` inside the extension).

2. **Mirror the deposit pattern**: Add a `swapper` field to the swap call that the pool passes explicitly (analogous to `owner` in `addLiquidity`), so the extension always receives the economically relevant actor regardless of the call path.

Until fixed, pools using `SwapAllowlistExtension` should not allowlist the router address and should document that allowlisted users must call the pool directly.

---

### Proof of Concept

```solidity
// 1. Deploy pool with SwapAllowlistExtension (allowAllSwappers = false)
// 2. Admin allowlists the router (intending to enable router-based swaps)
allowlistExt.setAllowedToSwap(pool, address(router), true);

// 3. Non-allowlisted attacker calls the router
// allowedSwapper[pool][attacker] == false, but the check is on router, not attacker
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:            address(pool),
    recipient:       attacker,
    zeroForOne:      true,
    amountIn:        1e18,
    amountOutMinimum: 0,
    priceLimitX64:   0,
    extensionData:   "",
    deadline:        block.timestamp
}));
// Swap succeeds — allowlist bypassed
// allowedSwapper[pool][router] == true  ← wrong identity checked
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
