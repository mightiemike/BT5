### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, enabling allowlist bypass or breaking router-mediated swaps for allowlisted users — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is always `msg.sender` of the pool's `swap` call. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the allowlist gates the **router address** rather than the **actual user**. This creates an irreconcilable dilemma: either the router is allowlisted (any user bypasses the per-user restriction) or it is not (allowlisted users cannot use the router at all). The deposit-side extension avoids this by checking `owner` (an explicit parameter), but the swap path has no equivalent.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every configured `beforeSwap` hook:

```solidity
// MetricOmmPool.sol:231
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim:

```solidity
// ExtensionCalling.sol:97
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, owner, salt, deltas, extensionData))
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

So `msg.sender` to the pool is the **router contract**, not the user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` ignores `sender` entirely and checks the explicit `owner` parameter — the economic actor — which the pool's `addLiquidity` carries separately from `msg.sender`:

```solidity
// DepositAllowlistExtension.sol:32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

The swap path has no equivalent explicit `swapper` parameter; the pool's `swap` signature only exposes `recipient`, not the originating user. This makes correct per-user gating structurally impossible through the router.

---

### Impact Explanation

Two mutually exclusive failure modes arise for any pool that configures `SwapAllowlistExtension` with per-user restrictions:

**Mode A — Allowlist bypass (High):** The pool admin allowlists the router address so that router-mediated swaps work. Because `allowedSwapper[pool][router] == true`, every user who calls `exactInputSingle` or `exactInput` on the router passes the check regardless of whether they are individually allowlisted. Unauthorized users can trade on a curated pool, exposing LP principal to adversarial or unintended counterparties.

**Mode B — Broken core functionality (Medium):** The pool admin allowlists only specific user addresses (not the router). Allowlisted users who call the router receive `NotAllowedToSwap` because the router's address is not in the allowlist. Since `pool.swap` requires a `IMetricOmmSwapCallback` implementation, users cannot call the pool directly without deploying a custom callback contract. The primary user-facing interface is effectively unusable for the very users the admin intended to serve.

Both modes are reachable by any unprivileged actor through the public `MetricOmmSimpleRouter` entrypoints.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the standard, documented user-facing swap interface.
- A pool admin who deploys a curated pool with `SwapAllowlistExtension` will naturally need to decide whether to allowlist the router. Either choice produces a broken invariant.
- No special privileges, malicious setup, or non-standard tokens are required. Any user with a token balance can trigger Mode A; any allowlisted user attempting a router swap triggers Mode B.
- The `generate_scanned_questions.py` audit target explicitly flags this path: *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."*

---

### Recommendation

Redesign the swap allowlist to gate the **economic actor** rather than the direct pool caller. Two approaches:

1. **Preferred — forward the originating user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the router to cooperate, but the extension can enforce that the decoded address matches a signed or trusted source.

2. **Alternative — add an explicit `swapper` parameter to `pool.swap`:** Mirror the deposit path's `owner` parameter. The pool accepts a `swapper` address (defaulting to `msg.sender` for direct calls), and the extension checks that address. The router passes `msg.sender` as `swapper`.

Until fixed, pools that require per-user swap gating should not rely on `SwapAllowlistExtension` for router-mediated flows.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router so users can swap
  - Pool admin does NOT allowlist attacker address

Attack (Mode A — bypass):
  1. attacker (not individually allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) with msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  5. Swap executes — attacker trades on the curated pool

Broken invariant:
  allowedSwapper[pool][attacker] == false, yet the swap succeeds.
  The per-user allowlist is completely bypassed for any user who routes through the router.
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
