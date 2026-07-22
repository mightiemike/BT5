### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Unauthorized Depositors to Bypass the Allowlist Guard - (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual caller of `addLiquidity`) and gates access only on `owner` (the position beneficiary). Because `addLiquidity` permits `msg.sender != owner`, any unprivileged actor can bypass the deposit allowlist by naming an allowlisted address as `owner`.

---

### Finding Description

The pool's `addLiquidity` function accepts a caller-supplied `owner` that may differ from `msg.sender`. When the pool fires the `beforeAddLiquidity` hook it passes both actors:

```
beforeAddLiquidity(sender = msg.sender, owner = owner, ...)
```

`DepositAllowlistExtension.beforeAddLiquidity` receives both but discards `sender` entirely:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol  line 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The unnamed first parameter is the actual depositor; only `owner` is checked. Compare with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual swapper) and discards `recipient`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

The asymmetry is the root cause: the deposit guard checks the wrong actor.

---

### Impact Explanation

Any address not on the allowlist can deposit tokens into a restricted pool by calling `addLiquidity(allowlistedOwner, salt, deltas, ...)` directly on the pool, or via `MetricOmmPoolLiquidityAdder.addLiquidityWeightedFor(pool, allowlistedOwner, ...)`. The extension's check passes because `allowlistedOwner` is in the mapping; the actual depositor (`msg.sender`) is never evaluated. The pool admin's configured access control is silently nullified for the deposit path, constituting an admin-boundary break: an unprivileged actor bypasses a factory-configured guard.

---

### Likelihood Explanation

The `addLiquidity` interface explicitly supports `msg.sender != owner` (the pool even documents this separation in `IMetricOmmPoolActions`). The periphery contract `MetricOmmPoolLiquidityAdder.addLiquidityWeightedFor` provides a ready-made entry point that accepts an arbitrary `owner`. Any actor who knows one allowlisted address can exploit this immediately with no special privileges, no malicious setup, and no non-standard tokens.

---

### Recommendation

Replace the unnamed first parameter with `sender` and gate on it, mirroring `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is instead to gate on `owner` (position beneficiary), the NatSpec and extension description must be corrected and the `addLiquidityWeightedFor` path must be separately hardened.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured; only `alice` is allowlisted (`allowedDepositor[pool][alice] = true`).
2. `bob` (not allowlisted) implements `IMetricOmmModifyLiquidityCallback` to pay tokens from his own balance.
3. `bob` calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. Pool calls `extension.beforeAddLiquidity(bob, alice, salt, deltas, extensionData)`.
5. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. Pool invokes `bob`'s callback; `bob` transfers tokens; `alice` receives LP shares.
7. `bob` has deposited into a pool he is explicitly barred from, bypassing the allowlist entirely.

Alternatively, step 3 can be replaced with `liquidityAdder.addLiquidityWeightedFor(pool, alice, salt, ...)` — no custom callback implementation required.

---

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-20)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L91-116)
```text
    uint80 salt,
    LiquidityDelta calldata weightDeltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    int8 minimalCurBin,
    uint104 minimalPosition,
    int8 maximalCurBin,
    uint104 maximalPosition,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(weightDeltas);
    _validatePositiveWeights(weightDeltas);
    _validateBinAndBinPosition(pool, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);

    try IMetricOmmPoolActions(pool)
      .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, owner, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
    }
  }
```
