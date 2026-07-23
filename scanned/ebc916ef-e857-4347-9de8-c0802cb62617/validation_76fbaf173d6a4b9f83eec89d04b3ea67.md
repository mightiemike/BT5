### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass Per-User Swap Restriction — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument forwarded by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` = router address. A pool admin who allowlists the router to enable router-based swaps inadvertently opens the gate to every user, including those not on the per-user allowlist.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check against the `sender` parameter: [1](#0-0) 

`sender` is the first argument the pool passes when it calls the extension. `ExtensionCalling._beforeSwap` (mirroring the pattern visible in `_beforeAddLiquidity`) forwards `msg.sender` of the `pool.swap()` call as `sender`: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`. At that point `msg.sender` inside the pool is the router contract, so `sender` forwarded to the extension is the router address — not the actual end-user.

The allowlist mapping is keyed `allowedSwapper[pool][swapper]`: [3](#0-2) 

For router-originated swaps the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The pool admin faces a binary choice:

| Admin action | Effect |
|---|---|
| Allowlist the router | Every user — including non-allowlisted ones — can swap through the router |
| Do not allowlist the router | No user can swap through the router, even allowlisted ones |

There is no configuration that restricts router-based swaps to a specific set of users.

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, protocol-owned accounts) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The attacker can execute swaps against the pool's liquidity without authorization, draining LP value at oracle-derived prices the pool admin intended to expose only to trusted parties. This is a direct loss of LP principal and a broken core pool invariant.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point documented in the periphery. Any user who discovers the allowlist can trivially route through the router instead of calling `pool.swap()` directly. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices.

### Recommendation

Pass the actual end-user address through the swap call chain so the extension can gate on it. Two concrete options:

1. **Encode the real swapper in `extensionData`** and have the extension decode and verify it (requires the router to cooperate and the extension to trust the pool's forwarding).
2. **Check `sender` against the router's caller**: have the router expose a `currentSwapper()` view and have the extension query it — but this introduces a reentrancy-style trust assumption.

The cleanest fix is to have the pool pass an additional `payer` / `originator` field distinct from `msg.sender` through the extension call, similar to how `addLiquidity` separates `sender` (payer) from `owner` (position beneficiary). Until then, `SwapAllowlistExtension` should document that it gates the direct caller of `pool.swap()`, not the end-user, and pool admins must not rely on it for per-user access control when the router is in use.

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension as a beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to allow their allowlisted users to trade via the router.
3. Attacker (address NOT in allowedSwapper[pool]) calls:
       MetricOmmSimpleRouter.exactInputSingle(pool, tokenIn, tokenOut, amountIn, ...)
4. Router calls pool.swap(recipient, zeroForOne, amount, priceLimit, extensionData).
5. Pool calls SwapAllowlistExtension.beforeSwap(sender=router, ...).
6. Extension checks allowedSwapper[pool][router] == true → passes.
7. Swap executes; attacker receives output tokens.
   Per-user allowlist is completely bypassed.
``` [1](#0-0) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-90)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L1-1)
```text
// SPDX-License-Identifier: MIT
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L111-113)
```text
  /// @notice Swap allowlist rejected `msg.sender`.
  /// @dev Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set; `simulateSwapAndRevert` does not, so a passing simulation does not imply an allowed live swap.
  error NotAllowedToSwap();
```
