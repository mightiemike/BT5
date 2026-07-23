### Title
`SwapAllowlistExtension.beforeSwap` checks the immediate caller (`sender`) instead of the actual economic actor, allowing allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to gate swaps on curated pools by swapper identity. Its `beforeSwap` hook checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` becomes the router's address, not the actual user's address. If the pool admin allowlists the router (a necessary step to enable any router-based swaps on a curated pool), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

**Root cause — wrong actor bound in the allowlist check:**

`SwapAllowlistExtension.beforeSwap` gates on `sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool. The pool always sets that argument to its own `msg.sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← sender = whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

**How the router changes the actor:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` of the pool:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The actual user's address is never inspected.

**The forced dilemma for pool admins:**

The pool admin controls the allowlist via `setAllowedToSwap`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L17-19
function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
``` [4](#0-3) 

To allow KYC'd or institutional users to use the router (the protocol's primary supported swap entrypoint), the admin must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] == true` satisfies the check for every caller of the router, regardless of who they are. The admin has no mechanism to say "router is allowed, but only for these specific users."

The two outcomes are both broken:
- Router **not** allowlisted → legitimate allowlisted users cannot use the router at all.
- Router **allowlisted** → every unprivileged user bypasses the allowlist via the router.

This is structurally identical to the external report's pattern: the guard checks one dimension of state (the immediate caller) but is blind to the relevant dimension (the actual economic actor), so a two-step path (user → router → pool) defeats the protection.

---

### Impact Explanation

Any user who is not on the allowlist can trade on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) whenever the router is allowlisted. The allowlist's purpose — restricting access to specific counterparties, KYC'd users, or institutional participants — is completely defeated. Unauthorized users gain access to restricted liquidity, can front-run allowlisted participants, and can drain LP positions at oracle-anchored prices that were only intended for vetted counterparties.

---

### Likelihood Explanation

The router is the protocol's primary, documented swap entrypoint. Any pool admin who wants allowlisted users to have a normal UX (slippage protection, multi-hop, deadline checks) must allowlist the router. This is a natural and expected operational step, not an exotic misconfiguration. The bypass is then available to any unprivileged user with no special access or capital requirement beyond the swap itself.

---

### Recommendation

The allowlist must bind to the actual economic actor, not the immediate pool caller. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks that address. This requires a coordinated convention between the router and the extension.

2. **Check `recipient` instead of (or in addition to) `sender`**: For single-hop swaps the recipient is often the real user. This is imperfect for multi-hop paths where the recipient is the router itself on intermediate hops.

3. **Separate router-aware allowlist**: Introduce a two-level check — `allowedCaller[pool][sender]` (for direct callers) and `allowedUser[pool][user]` (decoded from extensionData when the caller is a trusted router). The extension would verify the router is a known periphery contract before trusting the extensionData-supplied user address.

---

### Proof of Concept

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` in the `beforeSwap` order.
2. Admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is intended to swap.
3. Admin calls `setAllowedToSwap(pool, router, true)` — necessary so `userA` can use the router.
4. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. The pool calls `_beforeSwap(router, ...)`.
7. The extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. `userB`'s swap executes at oracle-anchored prices on the curated pool, bypassing the allowlist entirely.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
