### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the end user. If the router is allowlisted for a pool (the only way to let legitimate users use the router), every unprivileged user can bypass the allowlist by routing through the same public router contract.

---

### Finding Description

**Root cause — identity mismatch between the allowlist check and the actual swapper:**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that exact value against the per-pool allowlist: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`), the router is the direct caller of `pool.swap()`: [3](#0-2) 

So the allowlist evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**The inescapable dilemma for the pool admin:**

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Legitimate allowlisted users cannot use the router at all |
| Router **allowlisted** | Every unprivileged user can bypass the allowlist by routing through the router |

There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A pool admin deploys a restricted pool (e.g., a private market-making pool or a compliance-gated pool) and configures `SwapAllowlistExtension` to permit only specific counterparties. To let those counterparties use the standard router, the admin must allowlist the router address. Once the router is allowlisted, any unprivileged address can call `exactInputSingle` / `exactInput` / `exactOutput` on the router and execute swaps against the restricted pool. The allowlist guard is completely defeated. Unauthorized swaps can drain LP-owned token reserves, execute trades at oracle-derived prices the pool admin never intended to expose to the public, and break the pool's intended access model — constituting a direct loss of LP principal and a broken core pool invariant (admin-boundary break via an unprivileged public path).

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is a public, permissionless contract.
- Any user who observes that the router is allowlisted for a pool can immediately exploit this with a standard `exactInputSingle` call — no special privileges, no flash loan, no complex setup.
- Pool admins who want their allowlisted users to have a good UX will naturally allowlist the router, making the bypass trivially reachable.

---

### Recommendation

Pass the **end user's address** through the swap path so the extension can gate on the actual economic actor. Two concrete approaches:

1. **Encode the originating user in `extensionData`** and have the router include `msg.sender` there; the extension reads and verifies it (requires the extension to trust the router, so the router itself must be separately verified).
2. **Add a `swapper` field to the pool's `swap()` signature** (analogous to how `addLiquidity` separates `sender` from `owner`) so the router can pass the true end user while the pool still enforces callback settlement against `msg.sender`.

The `DepositAllowlistExtension` should be audited for the same pattern on the `addLiquidity` path, where `sender` vs. `owner` separation already exists but the checked identity must be confirmed to match the pool admin's intent.

---

### Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension configured.
2. Admin calls setAllowedToSwap(pool, router, true)  ← necessary for UX
3. Admin does NOT call setAllowedToSwap(pool, attacker, true)
4. Attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ...) → msg.sender = router
6. Pool calls _beforeSwap(router, ...) → extension checks allowedSwapper[pool][router] = true → PASSES
7. Attacker's swap executes against the restricted pool, bypassing the allowlist entirely.
``` [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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
