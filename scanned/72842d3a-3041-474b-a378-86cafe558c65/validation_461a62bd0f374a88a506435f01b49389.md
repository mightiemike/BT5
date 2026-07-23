### Title
SwapAllowlistExtension Gates the Router Address Instead of the End-User, Allowing Any Caller to Bypass the Per-User Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the immediate `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the end-user. A pool admin who allowlists the router (the only way to let allowlisted users trade via the router) simultaneously opens the pool to every user who calls the router, defeating the per-user allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol – swap()
_beforeSwap(
    msg.sender,   // ← router address when called through MetricOmmSimpleRouter
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (correct for the pool-identity guard), and `sender` is whoever called `pool.swap()`. When the call originates from `MetricOmmSimpleRouter`, `sender` equals the router's address.

**The structural trap:** A pool admin who wants allowlisted users to trade via the router must add the router to `allowedSwapper[pool]`. The moment the router is allowlisted, every user who calls `router.exactInputSingle()` (or any other router entry point) passes the check, because the extension sees `sender = router` and `allowedSwapper[pool][router] = true`. The actual end-user identity is never inspected.

---

### Impact Explanation

Any user can bypass a configured per-user swap allowlist by routing through `MetricOmmSimpleRouter`:

- Unauthorized parties gain swap access to pools intended to be restricted (e.g., KYC-gated, institutional-only, or partner-only pools).
- The pool admin has no on-chain mechanism to simultaneously allow router-mediated swaps for approved users and block unapproved users from using the same router path.
- Unauthorized swaps can drain pool liquidity at oracle-anchored prices, harming LPs who deposited under the assumption that only vetted counterparties would trade.

This breaks the core access-control invariant of the extension and constitutes an admin-boundary break reachable by any unprivileged caller.

---

### Likelihood Explanation

- The `SwapAllowlistExtension` is a production periphery contract explicitly designed for per-user gating.
- Any pool that (a) deploys the extension and (b) needs allowlisted users to access the router will inevitably allowlist the router, triggering the bypass.
- The attacker needs no special role, no privileged setup, and no non-standard token behavior — only a call to the public router.

---

### Recommendation

The extension must resolve the true end-user identity rather than the immediate caller. Two sound approaches:

1. **Pass the original initiator through the router.** Have `MetricOmmSimpleRouter` encode the real `msg.sender` inside `extensionData` and have the extension decode and verify it (requires a trusted router registry or a signed payload).

2. **Check `tx.origin` as a fallback only when `sender` is a known router.** This is fragile but avoids the registry requirement; it is acceptable only if the threat model excludes contract-based callers.

3. **Require direct pool interaction for allowlisted pools.** Document that pools using `SwapAllowlistExtension` must not allowlist any router or aggregator address, and enforce this at the extension's `setAllowedToSwap` setter by rejecting known router addresses.

The cleanest fix is option 1: the router should forward the original caller's address in a standardized field of `extensionData`, and the extension should read that field when `sender` is a registered router.

---

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  allowedSwapper[P][alice]   = true   (alice is the approved trader)
  allowedSwapper[P][router]  = true   (admin adds router so alice can use it)

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})

  Router calls:
    P.swap(recipient=bob, ...)   ← msg.sender = router

  Pool calls:
    E.beforeSwap(sender=router, ...)

  Extension evaluates:
    allowedSwapper[P][router] == true  →  check passes

  Result:
    bob's swap executes successfully despite never being allowlisted.
    The per-user allowlist is fully bypassed.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```
