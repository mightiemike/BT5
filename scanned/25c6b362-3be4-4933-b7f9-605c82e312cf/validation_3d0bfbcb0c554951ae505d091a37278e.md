### Title
`SwapAllowlistExtension` checks the router address instead of the originating user, allowing any actor to bypass the swap allowlist on curated pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router address**, not the original user. If the pool admin allowlists the router (the natural step to enable router-based swaps for legitimate users), every non-allowlisted user can bypass the gate by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, the `sender` forwarded to `_beforeSwap` is `msg.sender` — the direct caller of `pool.swap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol  lines 230-240
_beforeSwap(
    msg.sender,   // ← direct caller, not the economic originator
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

**Step 2 — `SwapAllowlistExtension` checks that `sender` value.**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  lines 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (correct). `sender` is whatever the pool passed — the direct caller of `pool.swap`.

**Step 3 — The router calls `pool.swap` directly, making itself the `sender`.**

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  lines 72-80
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

The router does **not** forward `msg.sender` (the original user) to `pool.swap`. The pool therefore sees `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]`.

**Step 4 — The bypass.**

A pool admin who wants legitimate allowlisted users to be able to use the router must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** caller of the router, including users who are explicitly not on the allowlist.

---

### Impact Explanation

Any non-allowlisted user can trade on a curated pool that is supposed to be restricted to specific counterparties (e.g., KYC'd traders, institutional partners, or whitelisted bots). The allowlist provides no protection once the router is allowlisted. This is a direct policy bypass with fund-impacting consequences: the pool's LP positions are exposed to counterparties the pool admin explicitly excluded, and any fee or adverse-selection protection the allowlist was meant to provide is nullified.

---

### Likelihood Explanation

The scenario is highly likely in practice:
1. Pool admins deploying curated pools with `SwapAllowlistExtension` will naturally want their allowlisted users to be able to use the standard router.
2. Allowlisting the router is the only way to enable that, so it is the expected operational step.
3. Once the router is allowlisted, the bypass is trivially reachable by any user with no special privileges, no malicious setup, and no non-standard tokens — just a normal `exactInputSingle` call.

---

### Recommendation

The `sender` forwarded to the extension must represent the **economic originator** of the swap, not the intermediate contract. Two complementary fixes:

1. **In the router:** pass the original `msg.sender` through to the pool as a dedicated `originator` field, or encode it in `extensionData` in a way the extension can verify.
2. **In `SwapAllowlistExtension`:** gate on the originator identity rather than the direct pool caller. One approach is to check `extensionData` for a signed or router-attested originator address, or to require that the direct caller is either an allowlisted EOA or a router that itself attests the originator.

Alternatively, document clearly that the router must **never** be allowlisted on curated pools, and that allowlisted users must call `pool.swap` directly — but this removes the usability of the router for curated pools entirely.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    to enable legitimate users to use the router.
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker (not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool: curated_pool,
           ...
       })
  2. Router calls pool.swap(...) — msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  5. Swap executes. Attacker trades on the curated pool.

Result: attacker bypasses the allowlist with zero special privileges.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
