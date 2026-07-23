### Title
`SwapAllowlistExtension` Gates on Router Address Instead of Original User, Allowing Any User to Bypass the Per-User Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the pool call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the original user. If the pool admin allowlists the router address to enable router-mediated swaps for their approved users, every non-allowlisted user can also swap freely through the same router, completely defeating the per-user gate.

---

### Finding Description

**Call path:**

```
User (non-allowlisted) → MetricOmmSimpleRouter.exactInputSingle()
  → IMetricOmmPoolActions(pool).swap(recipient, ..., extensionData)
      [msg.sender = router]
  → MetricOmmPool._beforeSwap(sender=router, ...)
  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
      checks: allowedSwapper[pool][router]  ← router is allowlisted → passes
```

**Root cause — `SwapAllowlistExtension.beforeSwap`:** [1](#0-0) 

The check is `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the first argument forwarded by the pool. The pool always sets that argument to its own `msg.sender`: [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` calls the pool, the pool's `msg.sender` is the router: [3](#0-2) 

The same applies to every router entry point (`exactInput`, `exactOutputSingle`, `exactOutput`) and to the recursive callback hops inside `_exactOutputIterateCallback`: [4](#0-3) 

**The dilemma the pool admin faces:**

| Admin configuration | Direct pool call | Router call |
|---|---|---|
| Allowlist individual users only | ✓ works for allowlisted users | ✗ router not allowlisted → reverts for everyone |
| Allowlist the router | ✗ per-user gate is gone | ✓ any user passes |

There is no configuration that simultaneously (a) lets allowlisted users use the router and (b) blocks non-allowlisted users from using the router. A pool admin who allowlists the router — the natural choice to enable the standard periphery — opens the gate to all users.

---

### Impact Explanation

The `SwapAllowlistExtension` is the only on-chain mechanism to restrict who may trade in a pool. Once the router is allowlisted, any address can call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) and the extension will pass, regardless of whether that address is individually approved. The allowlist invariant — *only approved addresses may swap* — is broken for all router-mediated swaps. Pools relying on this gate for compliance, access control, or economic design will silently accept trades from every user.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the protocol's primary user-facing swap interface. Pool admins who deploy a `SwapAllowlistExtension` will almost certainly need to allowlist the router so that their approved users can interact through the standard periphery. The bypass is therefore reachable through a routine, expected admin action, not a misconfiguration. Any unprivileged user can exploit it by calling the router directly.

---

### Recommendation

The extension must verify the **original user**, not the immediate pool caller. Two sound approaches:

1. **Pass the original sender through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted router check (verify `msg.sender` of the pool call is a known router before trusting the payload).

2. **Check both `sender` and a decoded original user**: The extension checks `allowedSwapper[pool][sender]` (direct calls) OR, when `sender` is a known router, decodes the real user from `extensionData` and checks `allowedSwapper[pool][realUser]`.

The simplest safe default is to document that the extension is **incompatible with router-mediated swaps** and revert if `sender` is any address other than an EOA or a specifically vetted contract.

---

### Proof of Concept

```solidity
// Pool admin sets up allowlist: only `alice` may swap.
swapAllowlist.setAllowedToSwap(pool, alice, true);

// Admin also allowlists the router so alice can use the standard periphery.
swapAllowlist.setAllowedToSwap(pool, address(router), true);

// Eve (not allowlisted) calls the router directly.
// pool.swap() sees msg.sender = router → allowedSwapper[pool][router] = true → passes.
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: eve,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: token0,
    extensionData: "",
    deadline: block.timestamp
}));
// Eve receives token1 despite never being allowlisted.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
