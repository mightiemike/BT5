### Title
`SwapAllowlistExtension` checks the router address instead of the actual end-user, allowing any unprivileged caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the value the pool passes as its first argument to the hook — which is always `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. If the router is allowlisted (which is required for any router-mediated swap to succeed on a restricted pool), every unprivileged user can bypass the allowlist by simply calling the router instead of the pool directly.

---

### Finding Description

**Root cause — wrong identity forwarded to the hook**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with its own `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the router address when the swap originates from `MetricOmmSimpleRouter`. The check therefore resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**The forced dilemma**

A pool admin who deploys `SwapAllowlistExtension` to restrict swaps to a curated set of addresses faces an impossible choice:

- **Do not allowlist the router** → all router-mediated swaps revert for everyone, breaking the primary user-facing entry point.
- **Allowlist the router** → the allowlist is silently voided; any address can bypass it by calling `MetricOmmSimpleRouter` instead of the pool directly.

There is no configuration that simultaneously allows legitimate router users and blocks non-allowlisted direct callers, because the extension cannot distinguish between them.

**Contrast with `DepositAllowlistExtension`**

The deposit-side extension avoids this problem because the pool passes the `owner` (position key) as the second argument to `beforeAddLiquidity`, not `msg.sender`. The owner is set by the caller of `addLiquidity`, so even when the `MetricOmmPoolLiquidityAdder` is used, the actual beneficiary address is what the extension checks. [3](#0-2) 

The swap extension has no equivalent mechanism — it only receives the immediate caller of `pool.swap()`.

---

### Impact Explanation

Any user can bypass a pool's `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter`. This breaks the admin-boundary invariant: the pool admin's intent to restrict swap access to a curated set of addresses is nullified by an unprivileged path. Pools designed for restricted access (e.g., institutional-only liquidity, whitelisted market-maker pools) become fully open to arbitrary swappers, exposing LP funds to unauthorized price impact and enabling unauthorized extraction of value from restricted pools.

---

### Likelihood Explanation

The trigger requires only that:
1. A pool is deployed with `SwapAllowlistExtension` configured (a documented, production-ready extension).
2. The router is allowlisted on that pool (necessary for any router-mediated swap to work).
3. An unprivileged user calls `MetricOmmSimpleRouter` instead of the pool directly.

All three conditions are reachable by any unprivileged actor with no special setup. The router is a public, permissionless contract. [4](#0-3) 

---

### Recommendation

The pool must forward the **original end-user identity** to the extension, not its own `msg.sender`. Two viable approaches:

1. **Pass the original caller through the router via `callbackData`**: The router encodes `msg.sender` into the `callbackData` it passes to `pool.swap()`. The pool then extracts and forwards this value as `sender` to `_beforeSwap`. The extension can verify the callback data is authentic because only the pool's callback can reach the extension.

2. **Check `recipient` instead of `sender`**: For swap allowlists, gate on the `recipient` argument (the address that receives output tokens) rather than the immediate caller. This is already available as the second argument to `beforeSwap` and correctly identifies the economic beneficiary regardless of routing path.

Option 1 is more general and preserves the original design intent. Option 2 is simpler but only works when the recipient equals the intended swapper.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  allowedSwapper[pool][router] = true   // required for router to work
  allowedSwapper[pool][alice] = false   // alice is NOT allowlisted

Direct call (correctly blocked):
  alice -> pool.swap(...)
  pool calls extension.beforeSwap(sender=alice, ...)
  allowedSwapper[pool][alice] == false  → revert NotAllowedToSwap ✓

Router bypass (incorrectly passes):
  alice -> router.exactInput(pool, ...)
  router -> pool.swap(recipient=alice, ...)
  pool calls extension.beforeSwap(sender=router, ...)
  allowedSwapper[pool][router] == true  → passes ✗

Result: alice executes a swap on a pool she is not authorized to access.
``` [2](#0-1) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L280-295)
```text
    uint256 packedSlot0Final = Slot0Library.loadPackedSlot0();
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
      extensionData
    );
```

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
