### Title
`SwapAllowlistExtension.beforeSwap` gates on the router's address instead of the original user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `MetricOmmPool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` resolves to the router's address, not the original user's address. If the pool admin allowlists the router (required for allowlisted users to use the router at all), every unpermissioned user can bypass the per-user restriction by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the original user
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool (correct), `sender` = whoever called `pool.swap()`.

When a user swaps directly: `sender` = user → checked correctly.

When a user swaps through `MetricOmmSimpleRouter`: the router calls `pool.swap(...)`, so `sender` = router address. The pool also calls `IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(...)` back to the router, confirming the router is the direct `msg.sender` of the pool. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable dilemma for any pool admin who deploys a `SwapAllowlistExtension`:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router at all — broken core functionality |
| Router **allowlisted** | Every non-allowlisted user can bypass the restriction by routing through the router |

The pool admin has no way to simultaneously (a) allow allowlisted users to use the router and (b) block non-allowlisted users from using the router.

---

### Impact Explanation

**Direct loss / policy bypass on curated pools.** A pool configured with `SwapAllowlistExtension` to restrict trading to a specific set of counterparties (e.g., KYC'd addresses, institutional partners) is fully bypassed by any public user who routes through `MetricOmmSimpleRouter`. The attacker receives the same swap output as an allowlisted user, draining pool liquidity and violating the LP's curation intent. This matches the "allowlist bypass" impact class: disallowed users can still trade on a curated pool.

---

### Likelihood Explanation

**Medium.** The bypass requires the pool admin to have allowlisted the router — a natural and expected configuration for any pool that wants to support the standard periphery. The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any pool admin who deploys a `SwapAllowlistExtension` and also wants router support will inevitably create this bypass. The attacker needs no special privileges: a single public call to `router.exactInput(...)` suffices.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **original user**, not the direct pool caller. Two sound approaches:

1. **Pass the original user through the router.** The router encodes the original `msg.sender` into `extensionData` and the extension decodes it. This requires a trusted router identity check inside the extension (only trust the encoded identity when `sender` is a known router).

2. **Check `sender` only when it is not a trusted router; otherwise check the decoded user from `extensionData`.** The extension reads a factory-registered router allowlist and, when `sender` is a known router, extracts the real user from `extensionData`.

3. **Require direct pool interaction for allowlisted pools.** Document and enforce that pools with `SwapAllowlistExtension` must not allowlist the router, and the router must not be used for such pools. This is operationally fragile but avoids code changes.

The cleanest fix is option 1: the router appends `abi.encode(msg.sender)` to `extensionData` for each hop, and the extension verifies `sender` is a known router before trusting the decoded identity.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is the only allowed swapper)
  - allowedSwapper[pool][router] = true  (admin allowlists router so alice can use it)
  - allowAllSwappers[pool] = false

Attack (bob is NOT allowlisted):
  1. bob calls MetricOmmSimpleRouter.exactInput(path=[pool], ..., recipient=bob)
  2. Router calls pool.swap(recipient=bob, ...) → msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → PASSES
  5. Swap executes; bob receives tokens despite not being on the allowlist

Direct call (correctly blocked):
  1. bob calls pool.swap(...) directly → msg.sender = bob
  2. SwapAllowlistExtension checks allowedSwapper[pool][bob] → false → REVERTS
```

The bypass is reachable on any production pool that (a) uses `SwapAllowlistExtension` with `allowAllSwappers = false` and (b) has allowlisted the router to support standard periphery access. [1](#0-0) [2](#0-1) [3](#0-2)

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
