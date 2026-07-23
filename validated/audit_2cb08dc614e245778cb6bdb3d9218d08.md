### Title
`SwapAllowlistExtension` gates on `msg.sender` (router) instead of the actual initiating user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the pool call. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool. The extension therefore checks whether the **router** is allowlisted, not the actual initiating user. Any user can bypass a curated pool's swap allowlist by routing through the official periphery router.

---

### Finding Description

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the `sender` argument: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` receives that value as its first parameter and checks it against the per-pool allowlist: [2](#0-1) 

The check is `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool. When a user calls the pool directly, `sender` = user — the check is correct. When a user calls through `MetricOmmSimpleRouter`, `sender` = router — the check evaluates the router's allowlist status, not the user's.

This creates an irreconcilable actor mismatch:

- If the pool admin allowlists the router (the natural action to let "official" periphery work), **every user** can bypass the individual-user allowlist by routing through it.
- If the pool admin does not allowlist the router, **no user** can swap through the router even if they are individually allowlisted, breaking the periphery for curated pools.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` parameter (the actual position beneficiary), not `msg.sender`: [3](#0-2) 

The asymmetry confirms the swap path has the wrong actor bound.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd market makers, whitelisted institutions, or protocol-controlled addresses) can be freely traded against by any unprivileged user simply by calling `MetricOmmSimpleRouter`. The allowlist guard is silently bypassed, exposing LP funds to trades the pool admin explicitly intended to block. This constitutes a broken core pool functionality and an admin-boundary break via an unprivileged path.

**Severity: High**

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, publicly documented periphery entry point. Any user aware of the router can exploit this without any special privilege. The pool admin has no on-chain mechanism to prevent router-mediated bypass while still allowing allowlisted users to trade through the router.

**Likelihood: High**

---

### Recommendation

Pass the **originating user** rather than `msg.sender` to the extension. Two viable approaches:

1. **Preferred — check `recipient` or add an `initiator` field**: Extend the `beforeSwap` hook signature to carry the true initiating address (e.g., via `extensionData` or a dedicated parameter), and have the router populate it. The extension then checks that address.

2. **Alternative — check `recipient`**: If the pool's swap semantics guarantee `recipient` equals the economic beneficiary, gate on `recipient` instead of `sender`. This is weaker if `recipient` can be set to a third party.

The deposit allowlist's pattern of checking `owner` (the actual beneficiary, not `msg.sender`) should be mirrored in the swap allowlist.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, router, true)` to allow the official router (or `setAllowAllSwappers(pool, true)` — either way the router passes).
3. Attacker (not individually allowlisted) calls `MetricOmmSimpleRouter.swap(...)` targeting the curated pool.
4. The pool calls `_beforeSwap(msg.sender=router, ...)`.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true` → no revert.
6. The attacker's swap executes successfully despite not being on the allowlist. [4](#0-3) [1](#0-0)

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
