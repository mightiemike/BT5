### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass the Per-User Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for any allowlisted user), every unprivileged address can bypass the per-user restriction by calling the router instead of the pool directly.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the hook.**

In `MetricOmmPool.sol` the `swap` function calls:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    zeroForOne,
    amountSpecified,
    priceLimitX64,
    packedSlot0Initial,
    bidPriceX64,
    askPriceX64,
    extensionData
);
``` [1](#0-0) 

When the call originates from `MetricOmmSimpleRouter`, `msg.sender` is the **router address**, not the end user.

**Step 2 — `SwapAllowlistExtension` checks that router address.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [2](#0-1) 

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()` — the router when routed.

**Step 3 — The bypass.**

The pool admin must allowlist the router (`allowedSwapper[pool][router] = true`) so that allowlisted users can reach the pool through the router at all. Once the router is allowlisted, the check `allowedSwapper[pool][sender]` passes for **every** user who calls through the router, because `sender` is always the router address regardless of who initiated the transaction. A user who is explicitly **not** on the allowlist can call `MetricOmmSimpleRouter.exactInputSingle(...)` and the `beforeSwap` hook will see `sender = router`, pass the check, and execute the swap.

The project's own audit-target document explicitly flags this concern:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [3](#0-2) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-internal actors) can be freely accessed by any address via `MetricOmmSimpleRouter`. The allowlist provides no protection for router-mediated swaps. Unauthorized traders can drain LP-owned liquidity at oracle-derived prices, causing direct loss of LP principal and breaking the core pool invariant that only approved actors may trade.

---

### Likelihood Explanation

- The router is a production periphery contract designed for public use.
- Any pool that uses `SwapAllowlistExtension` and also wants to support router-mediated swaps for its allowlisted users **must** allowlist the router, which simultaneously opens the bypass to everyone.
- No special privileges, flash loans, or unusual token behavior are required. A standard EOA calling `exactInputSingle` is sufficient.
- The `DepositAllowlistExtension` avoids this problem because it checks `owner` (the position owner supplied by the caller), not `sender`. The swap extension has no equivalent field for the true initiator. [4](#0-3) 

---

### Recommendation

Pass the **true initiator** through the swap path so the extension can gate on it:

1. Add a `payer` or `initiator` field to the `beforeSwap` hook signature (or encode it in `extensionData`).
2. In `MetricOmmSimpleRouter`, encode `msg.sender` (the end user) into `extensionData` before calling `pool.swap`, and have `SwapAllowlistExtension` decode and verify it.
3. Alternatively, gate on `recipient` if the pool's design guarantees the recipient is always the end user, or require the router to forward the real caller explicitly.
4. As a short-term mitigation, document that `SwapAllowlistExtension` only gates direct pool callers and must not be used with any public router.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router for allowlisted users
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)  // alice is NOT allowlisted

Attack:
  - alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
    → pool.msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] → true ✓
  - Swap executes; alice receives tokens from the restricted pool.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds — alice bypasses the per-user allowlist.
```

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

**File:** generate_scanned_questions.py (L657-663)
```python
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
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
