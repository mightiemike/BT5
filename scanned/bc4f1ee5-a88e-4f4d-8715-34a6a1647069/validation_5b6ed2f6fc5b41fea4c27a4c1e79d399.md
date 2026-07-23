### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so `sender` = router address. If the pool admin allowlists the router address (the natural step to enable router-based swaps for curated-pool users), every user — including those not individually allowlisted — can bypass the per-user gate by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is the value forwarded from `MetricOmmPool.swap()`:

```solidity
_beforeSwap(
    msg.sender,   // ← caller of pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
``` [3](#0-2) 

So `msg.sender` inside `pool.swap()` is the **router contract**, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants allowlisted users to be able to use the router must add the router address to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for **every** caller of the router, regardless of whether that caller is individually allowlisted. Any unprivileged user can then call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and the extension's guard is satisfied by the router's allowlist entry, not the user's.

The `DepositAllowlistExtension` does not share this flaw: it checks `owner` (the position owner passed explicitly to `addLiquidity`), which is correctly bound to the economic beneficiary regardless of who the immediate caller is. [4](#0-3) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, institutional LPs) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The pool's token reserves are exposed to unrestricted swaps at oracle-derived prices, directly violating the LP's expectation of a gated pool and constituting a direct loss of the curation guarantee that LPs deposited under. Severity: **High**.

---

### Likelihood Explanation

The router is the primary user-facing swap interface. A pool admin who wants allowlisted users to be able to use the router has no other option than to add the router address to the allowlist — the extension provides no mechanism to distinguish "router acting on behalf of an allowlisted user" from "router acting on behalf of anyone." This is a predictable operational step, not an exotic misconfiguration. Likelihood: **Medium-High**.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **economic actor**, not the immediate `msg.sender` of `pool.swap()`. Two viable approaches:

1. **Extension-data forwarding**: The router passes the actual user address in `extensionData`; the extension decodes and checks it. The router must be trusted to supply the correct address (it already stores `msg.sender` in transient context for callback settlement).
2. **Separate sender field**: Align with `DepositAllowlistExtension`'s pattern — check the `sender` argument only when it is not a known router, and require the router to embed the real user address in `extensionData` for verification.

---

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the curated user
3. Pool admin calls setAllowedToSwap(pool, router, true)  // to let alice use the router
4. Bob (not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool: pool,
           ...
       })
5. pool.swap() is called with msg.sender = router.
6. beforeSwap receives sender = router.
7. allowedSwapper[pool][router] == true  →  check passes.
8. Bob's swap executes on the curated pool, bypassing the per-user allowlist.
```

The root cause is identical in structure to the external report: a guard that is supposed to identify and block a specific actor instead evaluates a proxy identity (the router address) whose allowlist entry was added for a different purpose, silently passing the check for every caller of that proxy.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
