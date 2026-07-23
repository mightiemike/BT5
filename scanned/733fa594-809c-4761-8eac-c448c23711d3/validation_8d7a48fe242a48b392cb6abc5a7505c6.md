### Title
`SwapAllowlistExtension` gates the router address instead of the end-user when swaps are routed through `MetricOmmSimpleRouter`, allowing any unprivileged user to bypass the per-user swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` enforces a per-user allowlist by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the address the pool passes as the swap initiator. When a swap is routed through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension checks the router's address — not the actual end-user's address. If the pool admin allowlists the router (a natural configuration to enable router-mediated swaps for permitted users), every unprivileged user can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` is the sole enforcement point for the swap allowlist on curated pools:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

The pool calls `ExtensionCalling._beforeSwap`, passing `msg.sender` (the immediate caller of `pool.swap`) as the `sender` argument forwarded to every configured extension. When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract, so the extension evaluates:

```
allowedSwapper[pool][router]   // router address, NOT the end-user
``` [2](#0-1) 

The allowlist mappings are keyed by `(pool, swapper)`:

```solidity
mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
mapping(address pool => bool) public allowAllSwappers;
``` [3](#0-2) 

A pool admin who wants to allow router-mediated swaps for their allowlisted users will call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` returns `true` for every call that arrives through the router — regardless of who the actual end-user is. The end-user's address is never inspected. [4](#0-3) 

The `MetricOmmSimpleRouter` is a public, permissionless contract. Any address can call its `exact*` entry points. [5](#0-4) 

---

### Impact Explanation

Any non-allowlisted user can execute swaps on a curated pool that has `SwapAllowlistExtension` configured, as long as the router is allowlisted (which is the only way to enable router-mediated swaps for legitimate users). This is a direct admin-boundary break: the pool admin's access-control policy is silently voided for all router-mediated swaps. Pools designed for permissioned participants (e.g., KYC-gated, institutional, or RWA pools) are fully open to arbitrary swappers. The economic consequence is that unauthorized parties can drain liquidity at oracle-anchored prices, causing direct loss of LP principal.

---

### Likelihood Explanation

The trigger requires only that the pool admin has allowlisted the router — a routine and expected configuration for any pool that intends to support the standard periphery. No privileged access, no malicious setup, and no non-standard tokens are required. Any public user can exploit this by calling `MetricOmmSimpleRouter.exactInput` or `exactOutput` targeting the curated pool. [1](#0-0) 

---

### Recommendation

Pass the true end-user address through the call stack rather than relying on `msg.sender` at the pool boundary. Two concrete approaches:

1. **Router forwards the originating user**: `MetricOmmSimpleRouter` passes `msg.sender` (the actual user) as an explicit `sender` parameter in the `pool.swap(...)` call, and the pool forwards this value — not its own `msg.sender` — to `ExtensionCalling._beforeSwap`.

2. **Extension reads `tx.origin` as a fallback**: Not recommended in general, but acceptable in a closed periphery where `tx.origin` is always the economic actor.

The pool's `ExtensionCalling._beforeSwap` must bind `sender` to the address that is economically responsible for the swap, not the intermediate contract that relayed the call. [6](#0-5) 

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension at extension slot 0
  admin calls setAllowedToSwap(pool, alice, true)       // alice is the only allowed swapper
  admin calls setAllowedToSwap(pool, router, true)      // router allowlisted to support alice's router swaps

Attack:
  bob (not allowlisted) calls MetricOmmSimpleRouter.exactInput(
      pool    = curated_pool,
      tokenIn = token0,
      amountIn = X,
      ...
  )

  MetricOmmSimpleRouter calls pool.swap(...)
    → pool.msg.sender = router
    → ExtensionCalling._beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  ✓
    → swap executes for bob

Result:
  bob swaps on a pool he is not allowlisted for.
  The allowlist policy is completely bypassed.
  alice's allowlist entry is irrelevant; the router entry is the effective gate.
``` [7](#0-6) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-20)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L1-1)
```text
// SPDX-License-Identifier: MIT
```

**File:** metric-core/contracts/ExtensionCalling.sol (L1-1)
```text
// SPDX-License-Identifier: MIT
```
