### Title
SwapAllowlistExtension Gates the Router Address Instead of the End-User, Allowing Any User to Bypass the Swap Allowlist via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of the `pool.swap()` call. When users swap through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the allowlist checks the **router's address**, not the end-user's address. If the router is allowlisted (the natural operational setup), every user — including those explicitly excluded — can bypass the swap allowlist by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist: [1](#0-0) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (correct), and `sender` is whatever address the pool passes as the first argument to the hook. The pool passes the direct caller of `pool.swap()` as `sender`. When `MetricOmmSimpleRouter` mediates the swap, it is the direct caller of `pool.swap()`, so `sender = address(router)`. [2](#0-1) 

The allowlist check therefore resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

This creates two mutually exclusive broken states:

1. **Router is allowlisted** (the only way allowlisted users can swap through the router): `allowedSwapper[pool][router] = true` passes the check for **every** caller of the router, including addresses the pool admin explicitly excluded. Any non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle()` and the allowlist is silently bypassed.

2. **Router is not allowlisted**: Every router-mediated swap reverts with `NotAllowedToSwap`, even for users whose individual addresses are in the allowlist. The primary periphery entry point is completely unusable for allowlisted pools.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` parameter (the LP position owner), not the pool's `msg.sender`: [3](#0-2) 

The swap path has no equivalent owner-level identity forwarding.

---

### Impact Explanation

A pool configured with a `SwapAllowlistExtension` to restrict swaps to KYC'd, whitelisted, or otherwise vetted addresses can be fully bypassed by any user routing through `MetricOmmSimpleRouter`. The attacker receives output tokens from the pool's LP reserves without being an authorized swapper. LP providers deposited under the assumption that only vetted counterparties would trade against their liquidity; unauthorized swaps directly extract value from those reserves. This is a direct loss of LP principal / protocol-fee revenue above Sherlock thresholds when the pool holds meaningful liquidity.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any pool that uses `SwapAllowlistExtension` and also wants its allowlisted users to be able to use the router must allowlist the router itself — which is the natural operational choice. Once the router is allowlisted, the bypass is trivially reachable by any unprivileged user with no special setup. No admin action, malicious token, or privileged role is required beyond the normal pool configuration.

---

### Recommendation

The pool must forward the **originating user** identity to the extension, not just `msg.sender`. Two complementary fixes:

1. **In `MetricOmmSimpleRouter`**: pass the end-user's address as the `sender` argument to `pool.swap()` rather than relying on `msg.sender` propagation. The router already knows the user from the call parameters.

2. **In `SwapAllowlistExtension.beforeSwap`**: document that `sender` must be the economic actor, not the routing intermediary, and add an integration test that asserts a non-allowlisted user cannot bypass the check via the router.

Alternatively, mirror the deposit extension pattern: have the pool pass a dedicated "originator" field that periphery contracts populate with the true end-user address, analogous to how `owner` is passed explicitly for liquidity operations.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // router allowlisted so users can swap
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)
  - LP provider adds liquidity.

Attack:
  - attacker (not in allowlist) calls MetricOmmSimpleRouter.exactInputSingle(...)
  - Router calls pool.swap(sender=router, ...)
  - Pool calls SwapAllowlistExtension.beforeSwap(sender=router, ...)
  - Check: allowedSwapper[pool][router] == true  → passes
  - Swap executes; attacker receives token output from LP reserves.

Result:
  - Non-allowlisted attacker successfully swaps against a restricted pool.
  - SwapAllowlistExtension invariant broken: allowedSwapper[pool][attacker] == false
    but the swap was not blocked.
``` [1](#0-0) [3](#0-2)

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
