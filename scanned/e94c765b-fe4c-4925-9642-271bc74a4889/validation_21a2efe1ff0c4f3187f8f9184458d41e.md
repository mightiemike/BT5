### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass Individual Swap Restrictions via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. If the router is allowlisted (a natural configuration choice for usability), every user — including those not individually allowlisted — can bypass the per-user swap gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
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

`ExtensionCalling._beforeSwap` encodes this as the `sender` parameter of `IMetricOmmExtensions.beforeSwap`.

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) calls `pool.swap()`, `sender` is the router's address, not the end user's address.

A pool admin who wants to restrict swaps to a specific set of users (e.g., KYC'd addresses, whitelisted market makers) will:
1. Set `allowAllSwappers[pool] = false`
2. Allowlist specific user addresses via `setAllowedToSwap`
3. Also allowlist the router address so that allowlisted users can use the router for convenience

Step 3 is the trap: `allowedSwapper[pool][router] = true` means the extension passes for **any** caller who routes through the router, because the extension only sees `sender = router`. The individual user identity is invisible to the extension.

The `DepositAllowlistExtension` does not have this problem because it checks `owner` (the economic beneficiary of the position), not `sender` (the payer). The swap allowlist has no equivalent "economic actor" field — the `recipient` is the output receiver, not the initiator.

---

### Impact Explanation

Any user not on the individual allowlist can bypass the swap gate by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). The router is a single shared address; allowlisting it for usability collapses all individual restrictions into a blanket allow-all for router-mediated swaps.

Consequences:
- Restricted pools (e.g., compliance-gated, private market-maker pools, or pools in controlled rollout) lose their access control for all router-mediated swaps.
- Unauthorized users can trade at oracle prices in pools that were designed to exclude them, violating the intended security model and any off-chain compliance guarantees.
- If the restricted pool has specific LP protections that depend on only trusted counterparties trading (e.g., to prevent adverse selection or front-running of oracle updates), those protections are nullified.

---

### Likelihood Explanation

The bypass is reachable whenever:
1. A pool is configured with `SwapAllowlistExtension` and individual (non-`allowAll`) restrictions.
2. The router address is also allowlisted (to give allowlisted users a convenient entry point).

This is a natural and expected configuration: pool admins who want to restrict swaps to specific users will still want those users to be able to use the standard router. The admin has no way to achieve both goals simultaneously with the current design — allowlisting the router grants access to everyone, and not allowlisting it blocks even the intended users from using the router.

---

### Recommendation

The `SwapAllowlistExtension` should check the actual end-user identity rather than the immediate caller. Two approaches:

1. **`extensionData` forwarding**: Have the router encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and check it. This requires a convention between the router and the extension.

2. **Separate router-aware allowlist**: Add a `trustedRouter` mapping. When `sender` is a trusted router, decode the real user from `extensionData`; otherwise check `sender` directly.

3. **Documentation / invariant enforcement**: At minimum, document clearly that allowlisting the router is equivalent to `allowAllSwappers = true`, and add a factory-level or extension-level guard that prevents simultaneously having individual restrictions and a router allowlist entry.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowAllSwappers[pool] = false
  - allowedSwapper[pool][alice] = true        // alice is the intended user
  - allowedSwapper[pool][router] = true       // admin adds router for convenience

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool, ...})
  - router calls pool.swap(recipient, ...) → msg.sender = router
  - pool calls extension.beforeSwap(sender=router, ...)
  - extension checks allowedSwapper[pool][router] → true → passes
  - bob's swap executes successfully despite not being on the allowlist

Direct call check (confirming the gate exists):
  - bob calls pool.swap(...) directly → msg.sender = bob
  - extension checks allowedSwapper[pool][bob] → false → NotAllowedToSwap ✓

Result: the allowlist gate is bypassed for all router-mediated swaps.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L228-240)
```text
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
