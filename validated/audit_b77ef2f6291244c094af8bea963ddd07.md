### Title
`SwapAllowlistExtension` checks the router's address instead of the actual user, allowing any user to bypass per-user swap allowlisting by routing through `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` on the pool call, so `sender` = router address. If the router is allowlisted on a curated pool (which is required for routing to work at all), every user — including those explicitly blocked — can bypass the per-user allowlist by routing through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

The pool passes `msg.sender` as `sender` to the extension:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← direct caller, not the end user
    recipient,
    ...
)
``` [2](#0-1) 

When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the router is `msg.sender`, so `sender` = router address. The extension then checks whether the **router** is allowlisted, not the end user.

Contrast this with `DepositAllowlistExtension`, which correctly checks `owner` — the LP position owner explicitly passed by the caller — so the deposit guard correctly identifies the economic actor even when a periphery contract calls `addLiquidity`:

```solidity
// DepositAllowlistExtension.sol line 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

The deposit extension gates by `owner` (economic actor); the swap extension gates by `sender` (technical caller). This inconsistency means the swap allowlist is silently broken when routing is involved.

### Impact Explanation

A pool admin deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd institutions, whitelisted market makers) cannot simultaneously allow routing through `MetricOmmSimpleRouter`. If the router is allowlisted (the only way to enable routing), every user — including explicitly blocked addresses — can bypass the per-user allowlist by calling the router instead of the pool directly. LPs who deposited into a curated pool expecting only authorized counterparties are exposed to unrestricted trading activity, including front-running and arbitrage from blocked actors.

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router on a curated pool. This is a necessary step for any curated pool that also wants to support routing. Any user who knows the router address can then bypass the allowlist. No special privileges or unusual conditions are required beyond the router being allowlisted.

### Recommendation

Gate by the economic actor rather than the technical caller. Two options:

1. **Check `recipient` instead of `sender`** — for swaps, the recipient is the output beneficiary. This changes semantics but correctly identifies the end user when routing.
2. **Decode user identity from `extensionData`** — have the router embed the end user's address in `extensionData`, and have the extension decode and check it. This requires router cooperation but preserves full flexibility.
3. **Document the incompatibility** — explicitly state that pools using `SwapAllowlistExtension` must not allowlist `MetricOmmSimpleRouter`, and that routing is unsupported on curated pools.

The deposit extension's pattern (checking `owner`) is the correct model: the economic actor's address should be passed explicitly and checked, not the technical caller.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin allowlists `alice` as an authorized swapper: `setAllowedToSwap(pool, alice, true)`.
3. Pool admin allowlists `MetricOmmSimpleRouter` to enable routing: `setAllowedToSwap(pool, router, true)`.
4. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.swap(...)` targeting the curated pool.
5. The router calls `pool.swap(...)` with itself as `msg.sender`.
6. `_beforeSwap` passes `sender = router` to `SwapAllowlistExtension.beforeSwap`.
7. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `bob` successfully swaps on a pool he was explicitly blocked from, violating the curation policy. [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-42)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }

  function setAllowAllDepositors(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllDepositors[pool_] = allowed;
    emit AllowAllDepositorsSet(pool_, allowed);
  }

  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
  }

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
