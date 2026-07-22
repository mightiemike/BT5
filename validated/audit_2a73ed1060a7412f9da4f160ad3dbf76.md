### Title
SwapAllowlistExtension gates the router address instead of the actual user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the direct caller of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool and is therefore the `sender` forwarded to the extension. If the router is allowlisted (which is required for any router-mediated swap to succeed on a curated pool), every user — including those not individually allowlisted — can bypass the per-user gate by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the entity calling the extension) and `sender` is the first argument forwarded by the pool — which is the entity that called `pool.swap()`. [1](#0-0) 

The `IMetricOmmExtensions.beforeSwap` interface signature confirms `sender` is the first positional argument: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInput(...)`, the router calls `pool.swap(...)` on the user's behalf. The pool's `msg.sender` at that point is the **router**, not the end user. The pool forwards this as `sender` to the extension. The allowlist check therefore becomes:

```
allowedSwapper[pool][router]   // NOT allowedSwapper[pool][user]
```

For any router-mediated swap to succeed on a curated pool, the pool admin must allowlist the router. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** user who routes through it, regardless of whether that individual user is on the allowlist.

The two execution paths produce different identity checks:

| Path | `sender` seen by extension | Check |
|---|---|---|
| `user → pool.swap()` | `user` | `allowedSwapper[pool][user]` ✓ |
| `user → router → pool.swap()` | `router` | `allowedSwapper[pool][router]` — bypasses per-user gate |

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-controlled addresses) is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. The disallowed user can execute swaps, drain LP liquidity at oracle prices, and extract value from the pool — a direct loss of LP principal and a complete failure of the intended access-control invariant.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, publicly documented periphery entry point for swaps. Any user aware of the allowlist restriction can trivially route through the router instead of calling the pool directly. No privileged access, special tokens, or unusual setup is required. The router is a deployed, permissionless contract.

---

### Recommendation

The extension must check the **economically responsible actor** — the end user — not the intermediary. Two sound approaches:

1. **Pass the original user through the router**: Have `MetricOmmSimpleRouter` accept a `swapper` parameter and forward it as `sender` to the pool (requires a pool-level change to accept an explicit sender rather than using `msg.sender`).

2. **Check `tx.origin` as a fallback** (weaker, not recommended for general use): Only acceptable in narrow, non-composable contexts.

3. **Preferred — extension-level router awareness**: The extension can require that when `sender` is a known router, the router must also forward the original user address in `extensionData`, and the extension validates that address against the allowlist.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin allowlists only `alice` as an allowed swapper
  - Pool admin also allowlists `router` (required for router-mediated swaps to work)

Attack:
  - `bob` (not allowlisted) calls router.exactInput(pool, ...)
  - Router calls pool.swap(...) → pool's msg.sender = router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks: allowedSwapper[pool][router] == true  ✓
  - Bob's swap executes successfully despite not being allowlisted

Direct call (correctly blocked):
  - `bob` calls pool.swap(...) directly
  - Pool calls extension.beforeSwap(sender=bob, ...)
  - Extension checks: allowedSwapper[pool][bob] == false  → revert ✓
```

The same structural issue applies to `DepositAllowlistExtension` if `MetricOmmPoolLiquidityAdder` is allowlisted as an `owner`, though the deposit extension checks `owner` (not `sender`), so the exploitability depends on how the liquidity adder forwards the `owner` argument. [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
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
