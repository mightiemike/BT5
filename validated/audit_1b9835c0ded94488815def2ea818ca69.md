### Title
`SwapAllowlistExtension` checks router address as swapper, allowing any user to bypass per-user swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which is always `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the allowlist checks the **router address** instead of the **actual user**. If the pool admin whitelists the router to enable router-based swaps, every user bypasses the per-user allowlist entirely.

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

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

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the first argument forwarded from `MetricOmmPool.swap`:

```solidity
_beforeSwap(
    msg.sender,   // <-- always the direct caller of pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`, so `msg.sender` to the pool is the **router contract**, not the user. The allowlist therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates two mutually exclusive failure modes:

1. **Router not whitelisted**: Every router-based swap reverts with `NotAllowedToSwap`, making `MetricOmmSimpleRouter` completely unusable for any pool that has the `SwapAllowlistExtension` configured.
2. **Router whitelisted**: The per-user allowlist is silently voided — any address can swap through the router regardless of whether they appear in `allowedSwapper`.

The `DepositAllowlistExtension` does **not** share this flaw: it correctly checks `owner` (the position beneficiary), not `sender` (the operator/router), so the deposit path works correctly under the operator pattern. [3](#0-2) 

The `SwapAllowlistExtension` contract documentation states it "Gates `swap` by swapper address, per pool," but the implementation gates the **intermediary** (router), not the swapper. [4](#0-3) 

### Impact Explanation

A pool admin who deploys a restricted pool (e.g., institutional-only liquidity) with `SwapAllowlistExtension` will inevitably whitelist the router to allow normal UX. Once the router is whitelisted, every unprivileged address can swap against the pool by calling `MetricOmmSimpleRouter.exactInputSingle()`. Unauthorized swaps can drain token reserves, cause adverse price impact for LPs, and allow front-running by parties the pool was explicitly designed to exclude — constituting a direct loss of LP assets and broken core swap access control.

### Likelihood Explanation

The failure path requires only that the pool admin whitelists the router — a natural and expected operational step for any pool that intends to support the periphery router. No malicious setup, non-standard tokens, or privileged attacker role is required. Any unprivileged address can exploit this once the router is whitelisted.

### Recommendation

The `SwapAllowlistExtension` must check the **actual end-user** rather than the intermediary. Two viable approaches:

1. **Pass user identity through `extensionData`**: Have `MetricOmmSimpleRouter` encode the originating user address into `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check it instead of (or in addition to) `sender`.
2. **Check `recipient` as a proxy**: If the pool's design guarantees `recipient` is the beneficiary, check `recipient` instead of `sender`. This is weaker but avoids router coupling.
3. **Align with the deposit pattern**: Mirror `DepositAllowlistExtension` — check the beneficiary identity, not the operator identity.

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
3. Pool admin calls setAllowedToSwap(pool, router, true)  // needed for router UX
4. Bob (not whitelisted) calls router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient=bob, ...)
   → msg.sender to pool = router
   → _beforeSwap(sender=router, ...)
   → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
6. Bob's swap succeeds despite never being added to the allowlist.
``` [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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
