### Title
`SwapAllowlistExtension` Allowlist Bypassed via Router Intermediary — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. The pool passes `msg.sender` as `sender`, so when a user routes through `MetricOmmSimpleRouter`, the extension sees the router's address instead of the actual user's address. If the pool admin allowlists the router (the natural step to enable router-based swaps for permitted users), every unpermissioned user can bypass the allowlist by calling any of the router's `exact*` entry points.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the caller of the extension hook) and `sender` is the first argument forwarded by the pool. In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
)
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`), the router calls `pool.swap(...)` directly:

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

The pool's `msg.sender` is now the router, so the extension receives `sender = router_address`. The check becomes `allowedSwapper[pool][router]` — the actual user's identity is never evaluated.

The pool admin faces an impossible choice:
- **Allowlist the router** → every unpermissioned user bypasses the gate by routing through it.
- **Do not allowlist the router** → every allowlisted user is blocked from using the router.

There is no configuration that achieves "only allowlisted users may swap, including via the router."

### Impact Explanation

A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC-verified counterparties, institutional market makers, or participants in a private liquidity arrangement). Once the router is allowlisted — a necessary step for any allowlisted user who wants to use the standard periphery — the gate is open to all callers. Unpermissioned users can execute swaps at oracle-anchored prices against LP capital that was deposited under the assumption of a restricted counterparty set. This constitutes unauthorized access to pool liquidity and a violation of the LP's risk model, with direct exposure to adverse selection from actors the pool was designed to exclude.

### Likelihood Explanation

The trigger requires only that the pool admin allowlists the router, which is the expected operational step for any pool that wants to support the standard periphery. The bypass itself requires no special privilege, no flash loan, and no unusual token behavior — any EOA can call `exactInputSingle` on the public router. The combination of a natural admin action and a trivial user action makes exploitation highly likely once a permissioned pool is live.

### Recommendation

The extension must gate the **originating user**, not the immediate `msg.sender` of the pool call. Two sound approaches:

1. **Check `recipient` as a proxy for the intended beneficiary** — only viable if the pool's usage model guarantees recipient == user, which is not always true.
2. **Require the actual user identity to be passed explicitly** — the router should forward `msg.sender` as part of `extensionData`, and the extension should decode and verify it. This requires a coordinated change to the router and extension.
3. **Remove router allowlisting and require direct pool calls** for permissioned pools — operationally restrictive but closes the gap without code changes.

The cleanest fix is option 2: define a standard encoding for "originating caller" in `extensionData` that the router always populates, and have `SwapAllowlistExtension` decode and check that field when present.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]   = true   (alice is permitted)
  allowedSwapper[pool][router]  = true   (router allowlisted so alice can use it)
  allowedSwapper[pool][mallory] = false  (mallory is NOT permitted)

Attack:
  mallory calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient=mallory, ...)
    → pool calls extension.beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes — mallory receives output tokens

Result:
  mallory swaps successfully against a pool she was explicitly excluded from.
  The allowlist invariant is broken; LP capital is exposed to an unauthorized counterparty.
```

---

**Affected files:**

- [1](#0-0)  — `beforeSwap` checks `sender` (= router address when routed), not the originating user.
- [2](#0-1)  — pool passes `msg.sender` (= router) as `sender` to the extension hook.
- [3](#0-2)  — router calls `pool.swap` directly; no originating-user forwarding.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
