### Title
SwapAllowlistExtension Gates Router Address Instead of Actual User on Router-Mediated Swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` enforces its allowlist against `sender`, which is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`, making per-user curation impossible through the supported periphery path and enabling a full allowlist bypass whenever the router is itself allowlisted.

---

### Finding Description

The `SwapAllowlistExtension` is the protocol's mechanism for curated pools that restrict which addresses may swap:

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

`msg.sender` here is the pool (the extension caller). `sender` is the first argument forwarded by the pool from its own `swap()` call — i.e., the `msg.sender` of `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInput*()`, the router calls `pool.swap()` on the user's behalf, making the router the `msg.sender` at the pool boundary. The pool then passes the router address as `sender` to the extension.

The allowlist lookup therefore resolves to:

```
allowedSwapper[pool][router]   ← checked
allowedSwapper[pool][user]     ← never checked
```

This creates two failure modes:

1. **Router not allowlisted**: Every router-mediated swap reverts, even for users who are individually allowlisted. The supported periphery path is broken for curated pools.
2. **Router allowlisted** (pool admin adds the router as a trusted intermediary, a natural operational step): Every user — including those explicitly excluded — can bypass the allowlist by routing through `MetricOmmSimpleRouter`. The curation policy is silently voided.

The asymmetry with `DepositAllowlistExtension` confirms this is unintentional. The deposit extension explicitly checks `owner` (the second argument, the actual depositor), not the caller:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

The deposit side correctly separates payer from owner and gates the economic actor. The swap side has no equivalent forwarding mechanism — `sender` is always the immediate caller of `pool.swap()`, which is the router when the periphery is used.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd addresses, protocol-owned accounts, or whitelisted market makers) and then allowlists the `MetricOmmSimpleRouter` as a trusted periphery entry point inadvertently opens the pool to all users. Any address can call `router.exactInput*()` and the extension will pass because it sees `sender = router`. The pool admin's curation boundary is completely bypassed through the officially supported swap path. This constitutes an admin-boundary break: an unprivileged actor reaches a flow the pool admin intended to gate.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard user-facing swap entry point. Pool admins who configure `SwapAllowlistExtension` will naturally also need to allowlist the router to permit their intended users to trade via the periphery. The moment the router is allowlisted, the per-user gate collapses. This is a predictable operational step, not an exotic edge case. Likelihood is **High** for any curated pool that also permits router access.

---

### Recommendation

The pool should forward the originating user's address as `sender` rather than its own `msg.sender`. One approach: add an explicit `swapper` parameter to `pool.swap()` that the router populates with `msg.sender` (the end user), mirroring how `addLiquidity` accepts an explicit `owner`. Alternatively, `SwapAllowlistExtension` can read the user from `extensionData` if the router encodes it there, but this requires a coordinated convention. The cleanest fix is to make the pool pass the economic actor (the address that will receive or pay tokens) rather than the immediate caller.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use the periphery.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
5. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, extensionData)`.
6. Pool calls `extension.beforeSwap(router, recipient, ...)` — `sender = router`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps on a pool he was explicitly excluded from. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```
