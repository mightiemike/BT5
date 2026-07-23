### Title
SwapAllowlistExtension gates the router address instead of the actual user, enabling full allowlist bypass on curated pools — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end-user's address. A pool admin who allowlists the router (or any shared intermediary) inadvertently opens the gate to every user; conversely, a pool admin who does not allowlist the router locks out every allowlisted user who tries to trade through the standard periphery path.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the first argument to `_beforeSwap`, which forwards it verbatim to every configured extension as the `sender` parameter. [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension is the pool): [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any router entry-point), the router calls `pool.swap(...)`. At that point `msg.sender` inside the pool is the **router**, so `sender` forwarded to the extension is the **router address**. The allowlist lookup becomes:

```
allowedSwapper[pool][router]   // checked
allowedSwapper[pool][user]     // never checked
```

The pool admin's intent — "only these specific addresses may trade" — is never enforced against the actual economic actor. [3](#0-2) 

---

### Impact Explanation

**Scenario A — router is allowlisted:**
Any unpermissioned user routes through `MetricOmmSimpleRouter`. The extension sees `sender = router`, which is allowlisted, and the swap proceeds. The per-user allowlist is completely bypassed. Every trade that the pool admin intended to restrict to specific counterparties (KYC, institutional, etc.) is open to the public, directly harming LP positions on curated pools.

**Scenario B — router is not allowlisted:**
Allowlisted users who attempt to trade through the standard periphery path are blocked (`NotAllowedToSwap`). They must call the pool directly, which requires implementing the `IMetricOmmSwapCallback` interface themselves. The router — the primary supported entry-point — is unusable for the pool's intended participants, breaking core swap functionality.

Both outcomes are fund-impacting: Scenario A exposes LP capital to unauthorized counterparties; Scenario B makes the pool's liquidity inaccessible through the supported interface.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entry-point described in the protocol documentation. Any pool that deploys `SwapAllowlistExtension` and expects users to interact through the router will immediately hit one of the two broken states. No special privileges, flash loans, or unusual token behavior are required — a normal `exactInputSingle` call is sufficient.

---

### Recommendation

The extension must gate the **original human caller**, not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`:** The router encodes the actual user address into `extensionData`; the extension decodes and checks it. This requires the router to be trusted to supply the correct value.

2. **Check `tx.origin` as a fallback (with caveats):** Only acceptable if the protocol explicitly rules out smart-contract callers; otherwise it introduces its own bypass surface.

3. **Structural fix — add a `caller` field to the swap interface:** The pool could accept an explicit `caller` address (verified against `msg.sender` or a trusted router registry) and forward it to extensions, separating the routing intermediary from the economic actor.

---

### Proof of Concept

```
Setup:
  pool deployed with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, router, true)   // router allowlisted
  alice (non-allowlisted) wants to swap

Attack:
  1. alice calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  2. router calls pool.swap(recipient=alice, ...)
     → msg.sender inside pool = router
  3. pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension.beforeSwap receives sender=router
  5. check: allowedSwapper[pool][router] == true  → passes
  6. swap executes; alice receives tokens

Result:
  alice, who was never individually allowlisted, successfully swaps
  on a pool the admin intended to restrict to specific counterparties.
```

The symmetric DoS (Scenario B) is triggered by simply not allowlisting the router: any allowlisted user calling through the router receives `NotAllowedToSwap` because `allowedSwapper[pool][router]` is false, even though `allowedSwapper[pool][user]` is true. [4](#0-3)

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
