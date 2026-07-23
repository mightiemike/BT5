### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User on Router-Mediated Swaps, Allowing Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the address the pool passes as the first argument when it calls the extension hook. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of the pool's `swap` call, so the pool forwards the router address as `sender`. The extension therefore checks whether the **router** is allowlisted, not whether the **actual end user** is allowlisted. If the pool admin allowlists the router (or sets `allowAllSwappers = true` for the pool) to enable normal routing, any non-allowlisted user can bypass the curated allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` is the sole enforcement point for the per-pool swap allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (the pool calls the extension), and `sender` is the first argument the pool supplies when dispatching the hook through `ExtensionCalling._beforeSwap`. In `MetricOmmPool.swap`, the pool's `msg.sender` is whoever called `swap` — the router when the user enters through `MetricOmmSimpleRouter`. The pool passes that address as `sender` to every configured extension.

**Attack path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured, intending to restrict trading to a curated set of addresses (e.g., KYC'd counterparties).
2. Pool admin calls `setAllowedToSwap(pool, router, true)` (or `setAllowAllSwappers(pool, true)`) so that the router can forward swaps — a natural operational step.
3. Non-allowlisted user calls `MetricOmmSimpleRouter.exactInput(...)` or `exactOutput(...)`.
4. Router calls `pool.swap(...)`. Pool's `msg.sender` = router. Pool dispatches `extension.beforeSwap(router, ...)`.
5. Extension evaluates `allowedSwapper[pool][router]` → `true`. Hook passes.
6. Non-allowlisted user's swap executes on the curated pool, bypassing the intended access control.

The allowlist state `allowedSwapper[pool][actualUser]` is never consulted on the router path. The guard is configured and active, but the wrong identity is checked — a direct structural analog to the Saffron `withdraw_variable` not checking `admin_fixed_withdrawn`.

---

### Impact Explanation

**Direct loss / broken core functionality — Medium/High.**

- Curated pools (e.g., institutional, regulatory, or partner-restricted pools) lose their access-control guarantee. Any address can trade by routing through the supported periphery.
- If the pool admin does **not** allowlist the router, the inverse failure occurs: all allowlisted users are blocked from using the router, breaking the primary swap entrypoint for normal users.
- In either case the allowlist extension provides no meaningful protection on the router path, which is the dominant public swap surface.

---

### Likelihood Explanation

**Medium.**

- `MetricOmmSimpleRouter` is the documented, supported swap entrypoint. Most users and integrators will route through it.
- Pool admins who configure an allowlist will naturally also allowlist the router (or enable `allowAllSwappers`) to keep the pool usable — the exact condition that opens the bypass.
- No special privilege or unusual transaction ordering is required; a single `exactInput` call suffices.

---

### Recommendation

The pool must pass the **original end-user address** — not `msg.sender` of the `swap` call — as `sender` to extension hooks. Two complementary fixes:

1. **In `MetricOmmPool.swap`**: accept an explicit `sender` parameter (the originating user) and forward it to `ExtensionCalling._beforeSwap`, rather than using `msg.sender`. The router already knows the user's address and can supply it.

2. **In `MetricOmmSimpleRouter`**: pass the original `msg.sender` (the end user) as the `sender` argument when calling `pool.swap`, so the pool can forward the correct identity to extensions.

This mirrors how Uniswap v4 separates `sender` (the hook-visible initiator) from `msg.sender` (the immediate caller).

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]   = true   // alice is the curated user
  allowedSwapper[pool][bob]     = false  // bob is NOT allowed
  allowedSwapper[pool][router]  = true   // router allowlisted for normal operation

Direct swap (bob → pool.swap directly):
  pool.swap(...) called by bob
  → extension.beforeSwap(bob, ...)
  → allowedSwapper[pool][bob] == false → revert NotAllowedToSwap ✓

Router swap (bob → router → pool.swap):
  router.exactInput({path: [pool], ...}) called by bob
  → pool.swap(...) called by router
  → extension.beforeSwap(router, ...)
  → allowedSwapper[pool][router] == true → hook passes ✗
  → bob's swap executes on the curated pool
```

The allowlist is enforced on direct pool calls but silently bypassed on every router-mediated call, which is the primary public entrypoint.

---

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-25)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }
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
