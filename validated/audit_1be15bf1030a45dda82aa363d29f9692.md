### Title
`SwapAllowlistExtension` checks the router address as `sender` instead of the actual end-user, allowing any user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the first argument forwarded by the pool — which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the **router contract**, not the actual end-user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every user — including those not individually allowlisted — can bypass the curation policy by routing through the router.

---

### Finding Description

**Call chain for a direct swap (correct):**

```
user → pool.swap()
  msg.sender = user
  _beforeSwap(sender = user, ...)
  SwapAllowlistExtension.beforeSwap(sender = user)
  checks: allowedSwapper[pool][user]  ← correct actor
```

**Call chain through the router (broken):**

```
user → router.exactInputSingle() → pool.swap()
  msg.sender = router
  _beforeSwap(sender = router, ...)
  SwapAllowlistExtension.beforeSwap(sender = router)
  checks: allowedSwapper[pool][router]  ← wrong actor
```

In `MetricOmmPool.swap()`, `_beforeSwap` is called with `msg.sender` as the first argument: [1](#0-0) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router the `msg.sender` inside the pool: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct for pool identity) and `sender` is the router (wrong — should be the actual user): [3](#0-2) 

For router-mediated swaps to work at all on a curated pool, the pool admin must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, **any** user — regardless of their individual allowlist status — can call `router.exactInputSingle()` and the extension will pass because it sees `sender = router`, which is allowlisted.

The same structural issue exists for `exactInput`, `exactOutputSingle`, and `exactOutput` paths in the router. [4](#0-3) 

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd users, whitelisted market makers) is completely open to any user who routes through `MetricOmmSimpleRouter`. The bypass is unprivileged — any EOA can call the public router. The consequence is that disallowed users can execute swaps against the pool's liquidity, directly violating the pool's curation policy and potentially draining LP assets at prices the pool admin intended to restrict.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported periphery swap path. Any pool admin who deploys a curated pool and wants allowlisted users to be able to use the router must allowlist the router address, which simultaneously opens the pool to all users. The bypass requires no special knowledge or setup — any user who observes the router is allowlisted can exploit it immediately.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **economic initiator** of the swap, not the immediate caller of `pool.swap()`. Two options:

1. **Check `recipient` instead of `sender`** — the recipient is the address that receives output tokens and is set by the actual user in `exactInputSingle`. However, this is also spoofable.

2. **Preferred: pass the original caller through `extensionData`** — the router should encode `msg.sender` (the actual user) into `extensionData` and the extension should decode and check that address. The pool admin would then allowlist actual users, not the router.

3. **Alternative: remove router support for allowlisted pools** — document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and require direct `pool.swap()` calls on curated pools.

The core invariant that must hold: `allowedSwapper[pool][X]` must gate the same `X` that the pool admin intended to control, regardless of which supported public entrypoint reaches the pool.

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Pool admin allowlists the router so allowlisted users can use it
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// Pool admin does NOT allowlist attacker
// allowedSwapper[pool][attacker] == false

// Attacker bypasses allowlist via router:
vm.prank(attacker);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 10_000,
    amountOutMinimum: 0,
    recipient: attacker,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Succeeds: extension sees sender=router (allowlisted), not attacker
// Attacker receives token1 output from the curated pool
```

The extension call resolves `allowedSwapper[pool][router] == true` and passes, even though `allowedSwapper[pool][attacker] == false`. [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
