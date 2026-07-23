### Title
`DepositAllowlistExtension` Checks Position `owner` Instead of Transaction `sender`, Allowing Any Caller to Bypass the Deposit Gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` by **depositor address**. Its `beforeAddLiquidity` hook, however, validates the `owner` argument (the LP position holder) rather than the `sender` argument (the actual caller who provides tokens). Because `addLiquidity` accepts an arbitrary `owner`, any unprivileged caller can bypass the allowlist entirely by naming any already-authorized address as `owner`.

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`msg.sender` is the actual depositor (the party that will be called back to supply tokens); `owner` is the LP position recipient, freely chosen by the caller. The extension hook receives both as `sender` and `owner` respectively:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

The first parameter (`sender` / the actual caller) is silently discarded — note the unnamed `address,` — and only `owner` is tested against `allowedDepositor`. Because `owner` is caller-supplied, any address that is not on the allowlist can pass the gate by setting `owner` to any address that **is** on the allowlist.

The same bypass is reachable through `MetricOmmPoolLiquidityAdder.addLiquidityWeighted`, which forwards a caller-supplied `owner` directly into `pool.addLiquidity`:

```solidity
try IMetricOmmPoolActions(pool)
    .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) ...
``` [3](#0-2) 

The payer in the callback is always `msg.sender` of the periphery call (the unauthorized party), while the LP shares are credited to the authorized `owner`.

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may inject liquidity. Its bypass allows an unprivileged caller to:

1. **Add liquidity to arbitrary bins**, shifting the pool's internal price curve and `binTotals` in ways the admin did not authorize.
2. **Manipulate bid/ask execution prices** for subsequent swappers — a bad-price execution impact — because bin composition directly determines the marginal price returned by `SwapMath`.
3. **Undermine regulatory or compliance controls** the pool admin intended to enforce (e.g., KYC-gated pools).

The unauthorized depositor loses the tokens they supply (LP shares go to the named `owner`), but the pool state is permanently altered. If the attacker can also swap (no swap allowlist, or they are on the swap allowlist), they can profit from the price distortion they created.

### Likelihood Explanation

The bypass requires only a single direct call to `pool.addLiquidity` with any authorized address as `owner`. No special privileges, flash loans, or multi-step setup are needed. Any party aware of an authorized address (which is readable from `allowedDepositor` storage) can execute this immediately.

### Recommendation

Change the `beforeAddLiquidity` check to validate `sender` (the actual caller / token provider) rather than `owner`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual swapper):

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [4](#0-3) 

### Proof of Concept

```
Setup:
  - Pool deployed with DepositAllowlistExtension on beforeAddLiquidity order.
  - alice is added to the allowlist: allowedDepositor[pool][alice] = true.
  - bob is NOT on the allowlist.

Attack:
  1. bob calls pool.addLiquidity(alice, salt, deltas, callbackData, "").
  2. Pool calls _beforeAddLiquidity(msg.sender=bob, owner=alice, ...).
  3. Extension evaluates: allowedDepositor[pool][alice] == true → check passes.
  4. Pool calls bob's metricOmmModifyLiquidityCallback to collect tokens.
  5. bob supplies tokens; alice receives LP shares.
  6. Pool state (binTotals, bin prices) is modified by an unauthorized depositor.

Result:
  - bob successfully deposited into a deposit-gated pool.
  - The deposit allowlist invariant is broken.
  - Pool bin composition and marginal prices are altered without admin authorization.
``` [2](#0-1) [5](#0-4)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L106-108)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-38)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```
