### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals the pool's `msg.sender` (the router contract), not the end user. When a pool admin allowlists the router to permit router-mediated swaps, every user in the world can bypass the allowlist by routing through `MetricOmmSimpleRouter`. The guard checks the wrong identity — the exact structural analog to the roulette contract classifying number 36 as GREEN because it checked the wrong boundary.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `sender`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

From the pool's perspective `msg.sender` = router address, so `sender` delivered to the extension = router address. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][alice]`.

Two broken outcomes follow:

| Pool admin configuration | Result |
|---|---|
| Allowlists the router (natural, to permit router-mediated swaps) | **Every user bypasses the allowlist** — the guard is completely ineffective |
| Does not allowlist the router | **Every allowlisted user is blocked from using the router** — the guard is over-restrictive |

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position owner), which the pool passes correctly regardless of the calling intermediary: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to KYC'd or institutional addresses is rendered completely open when the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) and the extension will pass because it sees the allowlisted router address, not the actual caller. This breaks the core access-control invariant of the permissioned pool, allowing unauthorized principals to drain pool liquidity at oracle prices, extract fees, or manipulate pool state — all impacts that fall under the contest's "broken core pool functionality" and "admin-boundary break" categories.

---

### Likelihood Explanation

The router is the standard, documented entry point for swaps. Any pool admin who deploys a `SwapAllowlistExtension`-gated pool and also wants users to be able to use the router will naturally allowlist the router address. The bypass requires no special privileges, no flash loans, and no unusual token behavior — a single `exactInputSingle` call from any EOA suffices. Likelihood is high.

---

### Recommendation

The extension must gate on the **end user**, not the intermediary. Two approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires router cooperation and is trust-dependent.

2. **Check `sender` only when `sender` is not a known router, and require the real user to be encoded**: Add a registry of trusted routers; when `sender` is a trusted router, decode the real user from `extensionData` and gate on that address instead.

3. **Preferred — gate on `recipient` or require direct pool calls for permissioned pools**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the extension level by reverting when `msg.sender` (the pool) is called from a non-EOA `sender`.

---

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension
  admin allowlists router R: allowedSwapper[P][R] = true
  alice (not allowlisted) wants to swap

Attack:
  alice calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  router calls P.swap(recipient=alice, ...)
  pool calls _beforeSwap(sender=router, ...)
  SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[P][router] == true  ✓ passes
  swap executes — alice's swap goes through despite not being allowlisted

Result:
  SwapAllowlistExtension guard is completely bypassed.
  Any user can swap on the "permissioned" pool via the router.
``` [3](#0-2) [6](#0-5) [1](#0-0)

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
