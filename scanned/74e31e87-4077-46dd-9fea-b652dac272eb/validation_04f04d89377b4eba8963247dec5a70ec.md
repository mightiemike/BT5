### Title
SwapAllowlistExtension Gates on Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. The pool passes `msg.sender` of `pool.swap()` as `sender`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual user. The extension therefore checks the router's address, not the user's address. Any user who calls the router can bypass a swap allowlist that was intended to restrict individual swappers.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` checks that argument against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the first hook argument — the `msg.sender` of `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the original user:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
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

The pool sees `msg.sender = address(router)`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an inescapable dilemma for the pool admin:

| Admin action | Effect |
|---|---|
| Allowlist individual users only (not the router) | Allowlisted users **cannot** swap via the router; the check fails because `allowedSwapper[pool][router] = false` |
| Allowlist the router address | **All** users bypass the allowlist; any caller of `router.exactInputSingle/exactInput/exactOutputSingle/exactOutput` passes the check |

The same issue applies to all four router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) and to the recursive `_exactOutputIterateCallback` path, which also calls `pool.swap()` with `msg.sender = router`.

---

### Impact Explanation

A pool admin deploys a restricted pool (e.g., a private OTC desk or a permissioned LP pool) and configures `SwapAllowlistExtension` to gate swaps to specific counterparties. To allow those counterparties to use the standard router, the admin must allowlist the router address. Once the router is allowlisted, **any unprivileged user** can call `router.exactInputSingle()` and execute swaps against the pool at oracle-derived prices. LP funds are exposed to unrestricted trading, defeating the entire purpose of the allowlist guard. This is a direct loss of LP principal control and a broken core pool invariant (the allowlist guard is the pool's only swap-access control mechanism).

---

### Likelihood Explanation

The trigger requires only a standard router call — no special permissions, no malicious setup, no non-standard tokens. Any user who can call the public router can exploit this. The only precondition is that the pool admin has allowlisted the router address, which is the natural and expected configuration for any pool that intends to support router-mediated swaps. Likelihood is high whenever `SwapAllowlistExtension` is deployed with a non-`allowAll` policy and the router is in the allowlist.

---

### Recommendation

The extension must check the **actual end-user**, not the immediate caller of `pool.swap()`. Two approaches:

**Option A — Pass the original user through the router.** Add a `payer` / `originator` field to the swap call or use a separate transient-storage slot (analogous to how the router already stores `_getPayer()` in transient storage for the callback). The pool would forward this value as `sender` to the extension.

**Option B — Check `recipient` or require direct pool calls for allowlisted pools.** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and revert in `beforeSwap` if `sender` is a known router address, forcing allowlisted users to call the pool directly.

The minimal fix consistent with the existing architecture is Option A: store `msg.sender` of the router entry point in transient storage and expose it so the pool can forward it to extensions as the canonical `sender`.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, address(router), true)
    -- necessary so that any router-mediated swap can pass the check
  admin does NOT call setAllowedToSwap(pool, address(attacker), true)

Attack:
  attacker (non-allowlisted EOA) calls:
    router.exactInputSingle({
        pool: pool,
        tokenIn: token0,
        ...
    })

  Execution trace:
    router.exactInputSingle()          msg.sender = attacker
      pool.swap(recipient, ...)        msg.sender = router   ← pool sees router
        _beforeSwap(msg.sender=router, ...)
          SwapAllowlistExtension.beforeSwap(sender=router, ...)
            allowedSwapper[pool][router] == true  ← passes!
        swap executes, attacker receives output tokens
```

The attacker successfully swaps against a pool that was intended to block them. The allowlist guard is fully bypassed. [1](#0-0) [2](#0-1) [3](#0-2)

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
