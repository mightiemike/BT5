### Title
SwapAllowlistExtension gates the router address instead of the actual end-user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual end-user. The allowlist therefore gates the router address, not the human swapper. Any pool admin who allowlists the router (a natural action to enable router-based swaps for their users) inadvertently opens the gate to every user on the network.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The lookup is `allowedSwapper[pool][router]`.

**The trap:** A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist (`allowedSwapper[pool][router] = true`). The moment they do, the check passes for **every** caller of the router — including users who were never allowlisted — because the router is a shared public contract.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` argument (the position owner), not `sender` (the intermediary), so the deposit path does not have this flaw.

---

### Impact Explanation

Any user can swap on a curated/restricted pool (e.g., KYC-gated, institutional-only, compliance-restricted) by routing through `MetricOmmSimpleRouter` once the pool admin has allowlisted the router. The allowlist guard is completely bypassed, allowing unauthorized users to drain LP liquidity at oracle-quoted prices. This is a direct loss of the pool's intended access-control invariant and can result in LP funds being traded against by parties the pool was explicitly designed to exclude.

---

### Likelihood Explanation

The scenario is realistic and likely:

1. A pool admin deploys a restricted pool with `SwapAllowlistExtension`.
2. They allowlist specific user addresses for direct swaps.
3. Those users (or the admin) also want to use the router for UX convenience (slippage protection, multi-hop, etc.).
4. The admin adds `allowedSwapper[pool][router] = true` — a natural, expected action.
5. From that moment, any address on the network can call `MetricOmmSimpleRouter.exactInputSingle()` targeting that pool and bypass the allowlist.

No privileged access, no malicious setup, and no non-standard tokens are required. The attacker only needs to call a public router function.

---

### Recommendation

The allowlist should gate the **economic actor** (the human user), not the intermediary. Two complementary fixes:

1. **Pass the original user through the router.** The router already stores the original `msg.sender` in transient storage as the payer. The pool's `swap` signature could accept an explicit `swapper` address (analogous to `owner` in `addLiquidity`), and the router would forward `msg.sender` there.

2. **Alternatively, check `recipient` instead of `sender` in the extension**, since `recipient` is the address that receives the output tokens and is the economically relevant party. However, this changes semantics for pools where sender ≠ recipient.

The cleanest fix mirrors the deposit path: add an explicit `swapper` parameter to `pool.swap()` that the router populates with `msg.sender`, and have `SwapAllowlistExtension` check that field.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is the only intended swapper)
  - allowedSwapper[pool][router] = true  (admin adds router so alice can use it)

Attack:
  - bob (not allowlisted) calls:
      router.exactInputSingle({pool: pool, recipient: bob, ...})
  - Router calls pool.swap(bob, ...) with msg.sender = router
  - Extension checks allowedSwapper[pool][router] → true
  - Bob's swap executes successfully despite never being allowlisted

Result:
  - Bob bypasses the allowlist and trades on a restricted pool
  - LP funds are exposed to an unauthorized counterparty
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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
