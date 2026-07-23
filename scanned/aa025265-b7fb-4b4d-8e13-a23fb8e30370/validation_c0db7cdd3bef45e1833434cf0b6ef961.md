### Title
Swap Allowlist Bypassed via Router: `sender` Bound to Router Address, Not End User — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so `sender` = router address. If the pool admin allowlists the router (necessary to support any router-mediated swap on a restricted pool), every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and uses it as the identity to gate:

```solidity
// SwapAllowlistExtension.sol L37-38
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

`msg.sender` here is the pool (the extension is called by the pool). `sender` is whatever the pool passes — the pool's direct caller (`msg.sender` inside `pool.swap()`). When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract, so `sender` = router address. The allowlist then evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

The pool admin faces an inescapable dilemma:

- **Router not allowlisted**: All router-mediated swaps revert. Allowlisted users cannot use the router at all.
- **Router allowlisted**: The check becomes `allowedSwapper[pool][router] == true` for every user who routes through the router, regardless of whether that end user is individually allowlisted. The per-user gate is completely bypassed.

The `DepositAllowlistExtension` has the analogous structure — it checks `owner` (the LP position owner), not the payer/sender — but the economic impact is lower because LP shares are credited to `owner`, not the attacker. [2](#0-1) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The attacker trades on a pool they are not authorized to access, extracting value from LP positions that were priced and sized under the assumption of a restricted, trusted counterparty set. This is a direct admin-boundary break with fund-impacting consequences: LPs suffer adverse selection from untrusted counterparties the allowlist was designed to exclude.

---

### Likelihood Explanation

Any pool that (a) deploys `SwapAllowlistExtension` and (b) wants allowlisted users to be able to use the router must allowlist the router address. This is a normal operational requirement — the router is the primary user-facing swap entrypoint. Once the router is allowlisted, the bypass is available to every user with no special privileges, no flash loan, and no admin interaction. The trigger is a single public `MetricOmmSimpleRouter` call. [3](#0-2) 

---

### Recommendation

The pool must pass the **originating end user** as `sender` to the extension, not its own `msg.sender`. Two complementary fixes:

1. **Pool-level**: The pool's `swap()` function should accept an explicit `sender` parameter from the caller (the router passes `msg.sender` = end user) rather than binding `sender` to `msg.sender` inside the pool. This is the standard "router forwards originator" pattern.

2. **Extension-level**: `SwapAllowlistExtension.beforeSwap` should treat the `sender` argument as the end user and never rely on `msg.sender` (the pool) for identity. The current code already does this structurally — the fix is upstream in how the pool populates `sender`.

3. **Router-level**: `MetricOmmSimpleRouter` must forward `msg.sender` (the end user) as the `sender` argument when calling `pool.swap(...)`, not its own address. [4](#0-3) 

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin allowlists Alice (trusted user) via setAllowedToSwap(pool, alice, true).
  - Pool admin allowlists MetricOmmSimpleRouter via setAllowedToSwap(pool, router, true)
    (required so Alice can use the router).

Attack:
  1. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInput(...) targeting the pool.
  2. Router calls pool.swap(...); pool's msg.sender = router.
  3. Pool passes sender = router to SwapAllowlistExtension.beforeSwap.
  4. Extension evaluates: allowedSwapper[pool][router] == true → passes.
  5. Bob's swap executes on the restricted pool.

Result:
  - Bob, an unprivileged and non-allowlisted user, successfully trades on a pool
    that was configured to restrict access to Alice only.
  - LPs are exposed to adverse selection from an untrusted counterparty.
``` [1](#0-0) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
