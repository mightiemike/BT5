### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks `allowedSwapper[msg.sender][sender]`. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the `sender` argument forwarded to the extension is the router's address — not the end user's address. If the router is allowlisted (or `allowAllSwappers` is set to accommodate router-mediated flows), every user, including those the pool admin intended to exclude, can bypass the allowlist by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension` is designed to gate which addresses may swap on a curated pool:

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

`msg.sender` here is the pool (correct — enforced by `onlyPool`). `sender` is the address the pool passes as the initiating swapper. When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)`. At the pool level, `msg.sender` is the router, so the `sender` value forwarded into `ExtensionCalling._beforeSwap` and then into the extension is the **router's address**, not the end user's address.

The allowlist check therefore evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`.

The analog to the vesting pruning bug is structural: just as the pruning loop replaces an expired element with the last element and then increments the index — silently skipping the check on the newly placed element — the allowlist check here replaces the economically relevant actor (the user) with the intermediary (the router) and silently skips checking the intended subject of the guard.

---

### Impact Explanation

A pool admin who configures a curated pool with `SwapAllowlistExtension` intends to restrict swapping to a specific set of addresses. If the router is allowlisted (a natural operational step so that allowlisted users can use the standard periphery), the guard is completely bypassed for all users who route through `MetricOmmSimpleRouter`. Any non-allowlisted address can execute swaps on the curated pool by calling the public router instead of the pool directly. This defeats the entire purpose of the allowlist, allowing unauthorized parties to drain liquidity, front-run, or otherwise interact with a pool that was designed to be access-controlled. The impact is a direct policy bypass with fund-impacting consequences for LP positions in the curated pool.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any user who discovers the allowlist restriction on a direct pool call will naturally try the router as an alternative path. No privileged access, special tokens, or malicious setup is required — only a public router call. The likelihood is high whenever a pool uses `SwapAllowlistExtension` and the router is allowlisted or `allowAllSwappers` is enabled.

---

### Recommendation

The pool must pass the **original initiating user** as `sender` to the extension, not the immediate `msg.sender`. Two approaches:

1. **Pass the real user through the call chain**: The router should pass the end user's address explicitly as the `sender` argument when calling `pool.swap(...)`, and the pool should forward that value — not `msg.sender` — into `ExtensionCalling._beforeSwap`.

2. **Check both sender and recipient in the extension**: The extension can check whether `sender` is a known trusted router and, if so, fall back to checking the `recipient` or an authenticated caller embedded in `extensionData`.

The invariant that must hold: `allowedSwapper[pool][X]` must be evaluated where `X` is the address that economically benefits from and controls the swap, regardless of which periphery contract relays the call.

---

### Proof of Concept

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` attached.
2. Admin calls `setAllowedToSwap(pool, router, true)` so that allowlisted users can use the standard periphery.
3. Admin calls `setAllowedToSwap(pool, alice, true)` and intentionally does **not** allowlist `bob`.
4. `bob` calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the curated pool.
5. The router calls `pool.swap(...)`. The pool's `msg.sender` is the router.
6. `ExtensionCalling._beforeSwap` calls `extension.beforeSwap(router, ...)`.
7. The extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. `bob`'s swap executes successfully despite never being allowlisted. [1](#0-0) [2](#0-1)

### Citations

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
