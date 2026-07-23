### Title
SwapAllowlistExtension gates on router address instead of actual user when swaps route through MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` (the immediate caller of `pool.swap`). When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the actual user. The allowlist therefore checks the router's address, not the user's address, breaking the per-user curation invariant.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct, used as the namespace key), and `sender` is the first argument forwarded by the pool's `_beforeSwap` dispatcher:

```solidity
_beforeSwap(
    msg.sender,   // <-- pool's msg.sender = whoever called pool.swap()
    recipient,
    ...
)
``` [2](#0-1) 

The dispatcher passes it unchanged to the extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
``` [4](#0-3) 

So `msg.sender` to the pool is the **router address**, not the actual user. The extension receives `sender = router_address` and evaluates `allowedSwapper[pool][router_address]`, which is `false` unless the router itself was explicitly allowlisted.

The same applies to `exactOutputSingle`, `exactInput`, and `exactOutput`. [5](#0-4) 

---

### Impact Explanation

Two mutually exclusive failure modes, both fund-impacting:

**Mode A — Allowlisted users are silently blocked from the router.** A pool admin allowlists specific user addresses. Those users attempt to swap through the supported `MetricOmmSimpleRouter` periphery. The extension checks `allowedSwapper[pool][router]` = `false` and reverts. Allowlisted users cannot use the primary public swap path; the pool is effectively unusable for them through the router.

**Mode B — Allowlist bypass via router.** To fix Mode A, the admin allowlists the router address (`setAllowedToSwap(pool, router, true)`). Now `allowedSwapper[pool][router]` = `true`, so **any** user who routes through the router passes the check regardless of whether they are individually allowlisted. The per-user curation is completely bypassed. Any unprivileged address can swap on a pool that was intended to be restricted to a curated set of counterparties.

Mode B is the direct-loss path: LP funds in a curated pool are exposed to unrestricted swap flow, which may include adversarial actors the pool admin explicitly intended to exclude.

---

### Likelihood Explanation

- `SwapAllowlistExtension` is a production periphery contract, not a test mock.
- `MetricOmmSimpleRouter` is the primary public swap entrypoint documented in the protocol.
- Any pool that deploys `SwapAllowlistExtension` with per-user allowlisting and expects users to use the router hits this immediately.
- No special privileges, flash loans, or non-standard tokens are required. A normal user calling `exactInputSingle` through the router is sufficient to trigger either failure mode.

---

### Recommendation

The extension must identify the **economic actor** (the end user), not the immediate caller of `pool.swap`. Two options:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. This requires the extension to trust the router's encoding, which introduces a separate trust assumption.

2. **Check `recipient` instead of `sender`**: For swap allowlisting, the recipient is often the economic beneficiary. However, `recipient` can be a third party, so this is also imperfect.

3. **Preferred — router-aware identity forwarding**: Add a standardized identity field to the swap call path (e.g., a `payer` argument analogous to how `MetricOmmPoolLiquidityAdder` tracks `payer` in transient storage) so the pool can pass the true originator to extensions.

Until fixed, document that `SwapAllowlistExtension` only enforces correctly when users call `pool.swap` directly, not through any intermediary router.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook
  - Admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  - Admin does NOT allowlist the router address

Step 1 (direct call — works):
  alice calls pool.swap(...) directly
  → pool passes msg.sender = alice to _beforeSwap
  → extension checks allowedSwapper[pool][alice] = true → passes ✓

Step 2 (router call — blocked for alice):
  alice calls router.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(...)
  → pool passes msg.sender = router to _beforeSwap
  → extension checks allowedSwapper[pool][router] = false → reverts ✗

Step 3 (bypass — admin allowlists router to fix Step 2):
  admin calls setAllowedToSwap(pool, router, true)
  → now allowedSwapper[pool][router] = true
  → eve (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  → pool passes msg.sender = router to _beforeSwap
  → extension checks allowedSwapper[pool][router] = true → passes ✓
  → eve swaps on a pool she was never supposed to access
``` [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```
