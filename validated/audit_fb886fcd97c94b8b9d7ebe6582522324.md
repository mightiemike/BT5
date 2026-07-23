The code is clear enough to reach a definitive conclusion. Here is the full trace:

### Title
Router-Mediated Swaps Check Router Address Instead of End User in `SwapAllowlistExtension.beforeSwap` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against the per-pool allowlist. The pool always passes `msg.sender` as `sender`, so when a swap is routed through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the end user. If the router is allowlisted (which is required for any allowlisted user to use the router), every user — including non-allowlisted attackers — can bypass the allowlist by routing through the router.

---

### Finding Description

**Call chain — direct swap (correct):**

```
Alice → pool.swap(...)
  msg.sender in pool = Alice
  _beforeSwap(sender=Alice, ...)
  SwapAllowlistExtension.beforeSwap: allowedSwapper[pool][Alice] ✓
```

**Call chain — router-mediated swap (broken):**

```
Alice → MetricOmmSimpleRouter.exactInputSingle(...)
  router → pool.swap(recipient, ...)
    msg.sender in pool = router
    _beforeSwap(sender=router, ...)
    SwapAllowlistExtension.beforeSwap: allowedSwapper[pool][router] ← wrong actor
```

In `MetricOmmPool.swap`, the `sender` forwarded to the hook is always `msg.sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` passes that value unchanged as the first argument to every extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

In `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap()`: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

---

### Impact Explanation

The allowlist is designed to restrict swaps to a curated set of addresses (e.g., KYC'd users, whitelisted protocols). For allowlisted users to be able to use the router at all, the pool admin must add the router to the allowlist. Once the router is allowlisted, **any address** can call `MetricOmmSimpleRouter.exact*` targeting the restricted pool and the hook will pass — because it only sees `sender=router`, which is allowlisted. The allowlist is completely bypassed for all router-mediated swaps.

Impact: complete policy bypass on curated pools. Any non-allowlisted user can trade on a pool that was intended to be restricted.

---

### Likelihood Explanation

The router is the standard, documented periphery entry point for swaps. Pool admins who want allowlisted users to be able to use the router must allowlist it. This is the expected operational configuration, making the bypass trivially reachable by any public user with no special privileges.

---

### Recommendation

The extension must recover the true end-user identity. Two options:

1. **Pass the original `msg.sender` through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`, and `SwapAllowlistExtension.beforeSwap` decodes and checks it. This requires a convention between router and extension.

2. **Check `recipient` instead of `sender`**: If the pool's design intent is that the economic beneficiary (recipient) is the gated actor, check `recipient`. However, this changes the semantics of the allowlist.

3. **Dedicated router allowlist entry**: Document that the router cannot be used with `SwapAllowlistExtension` and revert if `sender` is a known router. This is the least invasive but limits composability.

The cleanest fix is option 1: the router should encode `msg.sender` into `extensionData` and the extension should decode and verify it, so the hook always gates the true initiating user regardless of intermediaries.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension
// Pool admin allowlists Alice and the router (so Alice can use the router)
extension.setAllowedToSwap(pool, alice, true);
extension.setAllowedToSwap(pool, address(router), true);

// Attack: Bob (not allowlisted) routes through the router
// Bob calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: restrictedPool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));

// Inside pool.swap(): msg.sender = router
// _beforeSwap(sender=router, ...)
// SwapAllowlistExtension checks: allowedSwapper[pool][router] == true → PASSES
// Bob's swap succeeds despite not being on the allowlist
```

The note in the question about `addLiquidity` with owner/payer separation is not relevant to this specific vulnerability path — the bypass is purely through the swap router, with no liquidity operation required.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-165)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
