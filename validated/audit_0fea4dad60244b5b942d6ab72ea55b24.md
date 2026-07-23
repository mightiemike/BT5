### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter — Router Address Replaces End-User Identity in Allowlist Check - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the **direct caller of `pool.swap()`**. When users route through `MetricOmmSimpleRouter`, `sender` becomes the router's address. If the pool admin allowlists the router (required for router-mediated swaps to function), every user — including non-allowlisted ones — can bypass the per-user swap restriction.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

where `msg.sender` is the pool and `sender` is the first argument forwarded by the pool. [1](#0-0) 

The pool's `swap()` passes its own `msg.sender` (the direct caller) as `sender` to the extension:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

When a user routes through `MetricOmmSimpleRouter`, the call chain is:

```
User → router.exactInputSingle(...)
     → pool.swap(...)          [msg.sender = router]
     → extension.beforeSwap(router, ...)
     → allowedSwapper[pool][router]  ← checked, NOT the end user
```

For router-mediated swaps to work at all, the pool admin must allowlist the router. Once the router is allowlisted, **every user** who routes through it passes the check, regardless of whether they are individually allowlisted.

The `DepositAllowlistExtension` does not share this flaw — it correctly gates on `owner` (the position owner, the economic actor), not on `sender` (the direct caller): [3](#0-2) 

The swap allowlist has no equivalent mechanism to recover the end user's identity from behind the router.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific addresses (KYC'd users, institutional counterparties, whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter` once the router is allowlisted. Non-allowlisted users can execute swaps, extract value from LP positions, and interact with pools designed to exclude them. This is a direct, fund-impacting allowlist bypass on a core pool access-control path. [4](#0-3) 

---

### Likelihood Explanation

Medium-High. `MetricOmmSimpleRouter` is the primary user-facing swap entry point. A pool admin who deploys a curated pool with `SwapAllowlistExtension` and also wants to support the standard router faces an unavoidable dilemma: allowlist the router (bypassing per-user control) or do not allowlist it (breaking router functionality for all users, including allowlisted ones). The natural operational action — allowlisting the router — silently voids the per-user allowlist. No malicious setup is required; the bypass is a consequence of normal, expected pool administration. [5](#0-4) 

---

### Recommendation

The extension must check the **economic actor** (the end user), not the **transport layer** (the router). Concrete options:

1. **Forward user identity via `extensionData`:** The router encodes the originating user's address into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.
2. **Add a `swapper` parameter to the pool's swap interface:** The pool accepts an explicit `swapper` address (verified against `msg.sender` or a trusted forwarder list) and passes it to extensions instead of raw `msg.sender`.
3. **Allowlist at the router level:** The router itself enforces a per-user allowlist before calling the pool, so the pool-level extension is not the sole enforcement point.

---

### Proof of Concept

```
Setup:
  pool admin deploys pool with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
  pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted for UX

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., extensionData: ""})

  router calls:
    pool.swap(recipient, zeroForOne, amount, priceLimit, callbackData, extensionData)
    // msg.sender = router

  pool calls:
    extension.beforeSwap(router, recipient, ...)
    // sender = router

  extension checks:
    allowedSwapper[pool][router] → true  ✓

  Result: bob's swap succeeds despite not being individually allowlisted.
  The invariant "only allowlisted users can swap" is broken.
```

The structural analog to the external report is exact: just as `SELFDESTRUCT` bypasses the Staker contract's transfer guard to inject ETH that breaks the balance invariant, routing through `MetricOmmSimpleRouter` bypasses the `SwapAllowlistExtension`'s per-user guard by substituting the router's address for the end user's address in the allowlist lookup. [6](#0-5) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
