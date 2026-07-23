### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address as `sender` instead of the end-user, allowing any caller to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` gates swaps by checking the `sender` argument that the pool passes into `beforeSwap`. That argument is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. If the router is allowlisted (the natural configuration for a pool that wants to support router-based swaps), every user — including those not individually allowlisted — can bypass the guard by calling any of the router's `exact*` entry points.

---

### Finding Description

**Call chain that exposes the bug:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         └─ pool.swap(recipient, ..., extensionData)   // msg.sender = router
               └─ _beforeSwap(msg.sender=router, ...)
                     └─ SwapAllowlistExtension.beforeSwap(sender=router, ...)
                           └─ allowedSwapper[pool][router]  ← checked, NOT the user
```

In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

where `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`. If the router is allowlisted, the guard passes for **every** user who calls through it, regardless of whether that user is individually allowlisted.

**Contrast with `DepositAllowlistExtension`:**

The deposit-side extension correctly ignores `sender` (the operator/adder) and gates `owner` (the economic beneficiary):

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

This allows `MetricOmmPoolLiquidityAdder` to act as an operator while still gating the position owner. `SwapAllowlistExtension` has no equivalent distinction — it gates the direct caller, not the economic actor.

**Two failure modes:**

| Router allowlisted? | Result |
|---|---|
| Yes | Any user bypasses the individual allowlist via the router |
| No | Individually allowlisted users cannot use the router at all |

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC-verified market makers, whitelisted counterparties) is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. The unauthorized user receives pool output tokens at oracle-anchored prices, draining LP value that was intended to be accessible only to vetted counterparties. This is a direct loss of LP principal through unauthorized swap settlement — matching the "swap conservation failure / admin-boundary break" impact gate.

---

### Likelihood Explanation

The router is the canonical user-facing entry point for the protocol. A pool admin who deploys a `SwapAllowlistExtension` to restrict swaps to specific users will almost certainly also need to allowlist the router so that those users can interact via the standard periphery. The moment the router is allowlisted, the individual allowlist is rendered inoperative for all router-mediated swaps. The trigger requires no privileged access — any unprivileged user can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router.

---

### Recommendation

Gate the economic actor, not the intermediary, mirroring the pattern already used by `DepositAllowlistExtension`. For swaps the closest equivalent to `owner` is the address that initiated the swap. Two concrete options:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender`**: The `recipient` is the address that receives output tokens and is the economic beneficiary of the swap. The extension already receives `recipient` as its second argument (currently ignored). Checking `allowedSwapper[pool][recipient]` would correctly gate the economic actor regardless of which intermediary called the pool.

Option 2 is the simpler fix and is consistent with the `DepositAllowlistExtension` pattern of gating the economic beneficiary.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)      // alice is KYC'd
  - Pool admin calls setAllowedToSwap(pool, router, true)     // allow router-based swaps

Attack:
  - Bob (not allowlisted) calls:
      router.exactInputSingle({pool: pool, ..., recipient: bob})
  - Router calls pool.swap(bob, ...) with msg.sender = router
  - Pool calls extension.beforeSwap(sender=router, recipient=bob, ...)
  - Extension checks allowedSwapper[pool][router] → true
  - Bob's swap executes successfully, bypassing the allowlist
  - Bob receives pool output tokens; LP funds are drained by an unauthorized party
```

The bypass is reachable through all four router entry points: `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
