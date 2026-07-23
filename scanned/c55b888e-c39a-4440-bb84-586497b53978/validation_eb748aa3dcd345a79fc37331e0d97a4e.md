### Title
`SwapAllowlistExtension` checks the router address instead of the end user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the first argument forwarded by the pool — which is always `msg.sender` from the pool's perspective, i.e., the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` mediates a swap, `sender` equals the router's address, not the end user's address. If the pool admin allowlists the router (a natural step to let allowlisted users use the router), every user — including non-allowlisted ones — can bypass the guard by routing through `MetricOmmSimpleRouter`.

---

### Finding Description

**Allowlist check identity mismatch**

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
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

The pool passes `msg.sender` as `sender` to every extension hook:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
```

`MetricOmmSimpleRouter` calls `pool.swap()` directly, making itself `msg.sender` to the pool:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. In every case the router is `msg.sender` to the pool, so `sender` in the extension = router address.

**Resulting invariant break**

| Scenario | `sender` seen by extension | Allowlist check |
|---|---|---|
| User calls `pool.swap()` directly | user address | `allowedSwapper[pool][user]` ✓ |
| User calls `router.exactInputSingle()` | **router address** | `allowedSwapper[pool][router]` ✗ |

The pool admin cannot simultaneously:
1. Allow allowlisted users to swap via the router (requires allowlisting the router address)
2. Block non-allowlisted users from swapping via the router

Because the extension cannot distinguish between different end users behind the same router address.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., a curated set of market makers or KYC'd addresses) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The attacker:

1. Calls `router.exactInputSingle()` (or any multi-hop variant) targeting the allowlisted pool.
2. The router calls `pool.swap()` with itself as `msg.sender`.
3. The extension checks `allowedSwapper[pool][router]`; if the router is allowlisted, the check passes.
4. The swap executes against LP positions that were intended to be accessible only to allowlisted parties.

This constitutes unauthorized swap execution against LP assets — a direct loss-of-principal path for LPs who deployed capital under the assumption that only trusted counterparties could trade against them.

---

### Likelihood Explanation

The bypass is triggered whenever the pool admin allowlists the router address. This is a natural and expected configuration: allowlisted users who want to perform multi-hop swaps or use slippage-protected wrappers must go through the router, so the admin allowlists it. The moment the router is allowlisted, the guard is open to all users. No malicious admin action is required — the vulnerability is a design flaw in identity propagation.

---

### Recommendation

The `sender` forwarded to extensions must reflect the **economic actor** (the end user), not the intermediary contract. Two complementary fixes:

**Option A — Router forwards original caller via `extensionData`**

Encode the original `msg.sender` into `extensionData` at the router level and have `SwapAllowlistExtension` decode and verify it. This requires a convention between the router and the extension.

**Option B — Pool exposes an `originSender` parameter (preferred)**

Add an `originSender` field to the `beforeSwap` / `afterSwap` hook signatures. The pool sets it to `msg.sender` for direct calls and the router passes the original `msg.sender` explicitly:

```diff
// MetricOmmSimpleRouter.sol
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...,
+   msg.sender,   // originSender
    params.extensionData
);
```

```diff
// SwapAllowlistExtension.sol
- function beforeSwap(address sender, ...) {
-     if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
+ function beforeSwap(address, address, ..., address originSender, ...) {
+     if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][originSender]) {
```

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// The pool admin also allowlists the router so allowedUser can use it.
swapExtension.setAllowedToSwap(address(pool), allowedUser, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true); // ← required for router use

// Attack: non-allowlisted user bypasses the guard via the router.
vm.prank(nonAllowlistedUser);
// Direct call reverts:
// pool.swap(...) → NotAllowedToSwap ✓

// Router call succeeds — extension sees sender=router, which IS allowlisted:
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: nonAllowlistedUser,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// ↑ succeeds — allowlist bypassed
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
