Based on my investigation of the extension framework, I found a confirmed wrong-actor binding issue in the `SwapAllowlistExtension`. Here is my analysis:

---

### Title
`SwapAllowlistExtension.beforeSwap` checks the immediate pool caller (`sender`) rather than the economic actor, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
The `SwapAllowlistExtension` gates swaps by checking the `sender` argument passed to `beforeSwap`. That argument is the `msg.sender` of the `pool.swap()` call — i.e., the immediate caller of the pool, not the end user. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. If the router is added to the allowlist (a natural operational step to enable routing on a curated pool), every user — including those explicitly excluded — can bypass the per-user gate by routing through the router.

### Finding Description
`SwapAllowlistExtension.beforeSwap` is:

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

`msg.sender` here is the pool (the contract calling the extension hook). `sender` is the first argument the pool forwards — the `msg.sender` of the originating `pool.swap()` call. When `MetricOmmSimpleRouter` executes a swap, it calls `pool.swap(...)` directly; the pool's `msg.sender` is therefore the router, and the pool forwards the router address as `sender` to the extension. The extension then evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`.

The pool admin who wants to allow legitimate routing must add the router to the allowlist. Once the router is allowlisted, the per-user gate is completely bypassed for every user who routes through it — including users the admin explicitly excluded.

The same structural flaw exists in `DepositAllowlistExtension.beforeAddLiquidity`, which ignores its `sender` argument entirely and checks `owner` (the LP position owner). If `MetricOmmPoolLiquidityAdder` passes itself as `owner` rather than `msg.sender`, the allowlist check is on the wrong address. However, the swap path is the more directly exploitable surface because the router is a standard, supported periphery entry point.

### Impact Explanation
A curated pool using `SwapAllowlistExtension` to restrict trading to a specific set of counterparties (e.g., KYC'd addresses, whitelisted market makers) is rendered ineffective. Any address can trade on the pool by routing through `MetricOmmSimpleRouter`. This is a direct policy bypass with fund-impacting consequences: the pool's price impact, fee revenue, and LP exposure are all affected by trades from actors the pool admin intended to exclude.

### Likelihood Explanation
The trigger requires only that the router be present in the allowlist — a natural and expected operational step for any curated pool that also wants to support standard routing. No privileged access, malicious setup, or non-standard token behavior is required. Any user can execute the bypass by calling the router rather than the pool directly.

### Recommendation
The extension must identify the economic actor independently of the immediate pool caller. Two sound approaches:

1. **Pass the originating user through `extensionData`**: The router encodes the actual user address in `extensionData`; the extension decodes and verifies it. This requires the pool to enforce that `extensionData` is not forgeable (e.g., signed by the pool admin or the router itself).
2. **Check `sender` against a router registry and then verify the user from `extensionData`**: If `sender` is a known router, decode the real user from `extensionData` and apply the allowlist check to that address.

The simplest safe default is to reject any `sender` that is not itself in the allowlist **and** is not the pool's registered router, then require the router to attest the real user identity in `extensionData`.

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is permitted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is added to enable standard routing.
4. `userB` (not in the allowlist) calls `MetricOmmSimpleRouter.swap(...)` targeting the pool.
5. The router calls `pool.swap(...)`. The pool's `msg.sender` is the router.
6. The pool calls `extension.beforeSwap(router, ...)`.
7. The extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `userB` successfully trades on a pool from which they were explicitly excluded. [1](#0-0) [2](#0-1) [3](#0-2)

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
