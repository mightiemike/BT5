### Title
`DepositAllowlistExtension` gates on `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` ignores the `sender` argument (the actual depositor) and instead checks the `owner` argument (the position beneficiary). Because `owner` is freely chosen by the caller, any unauthorized address can bypass the deposit allowlist by supplying an already-allowlisted address as `owner`, while paying for the deposit themselves through the `MetricOmmPoolLiquidityAdder`.

---

### Finding Description

The pool calls the `beforeAddLiquidity` hook with two identity arguments: `sender` (the direct caller of `pool.addLiquidity`) and `owner` (the position owner chosen by the caller). [1](#0-0) 

The extension receives both but silently discards `sender` (the unnamed first parameter) and gates only on `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts an arbitrary `owner` address from the caller. Its only validation is `owner != address(0)`: [3](#0-2) 

The adder then calls `pool.addLiquidity(owner, ...)` with the attacker-supplied `owner`, while recording `msg.sender` (the unauthorized caller) as the `payer` in transient storage: [4](#0-3) 

The hook sees `owner = allowedOwner` (passes the allowlist), tokens are pulled from the unauthorized payer, and LP shares are minted to `allowedOwner`. The actual depositor (`sender = LiquidityAdder`) is never checked.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly gates on `sender` (the direct caller), not on `recipient`: [5](#0-4) 

The two extensions are architecturally inconsistent: the swap guard checks the right identity; the deposit guard does not.

---

### Impact Explanation

An unauthorized address can add liquidity to any pool protected by `DepositAllowlistExtension` by:

1. Identifying any address `A` that is on the pool's deposit allowlist.
2. Calling `LiquidityAdder.addLiquidityExactShares(pool, A, salt, deltas, ...)`.
3. The hook checks `allowedDepositor[pool][A]` → passes; tokens are pulled from the attacker; LP shares are minted to `A`.

Consequences:
- **Allowlist policy is fully nullified**: the pool admin's intent to restrict depositors is defeated by any caller with capital.
- **Unauthorized liquidity manipulation**: the attacker can concentrate or dilute liquidity in specific bins, shifting the pool cursor and altering the effective bid/ask spread seen by all subsequent swaps.
- **LP principal dilution**: existing LPs' share of spread and notional fees is reduced by the injected shares, constituting a direct reduction in owed LP assets.
- The `addLiquidityWeighted` probe path compounds this: the probe call also passes through `beforeAddLiquidity` with the attacker-supplied `owner`, leaking pool state (token needs per bin) to the unauthorized caller before the paying deposit executes. [6](#0-5) 

---

### Likelihood Explanation

- The `LiquidityAdder` is a public, permissionless periphery contract.
- The attacker only needs to know one allowlisted address (observable on-chain from `AllowedToDepositSet` events or `allowedDepositor` reads).
- No privileged access, no special token behavior, and no malicious pool setup is required.
- The attack is executable in a single transaction.

---

### Recommendation

Gate on `sender` (the actual depositor / direct caller of `pool.addLiquidity`) rather than `owner` (the freely chosen position beneficiary):

```solidity
// Before (wrong):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// After (correct):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
``` [2](#0-1) 

This aligns the deposit guard with the swap guard's pattern and ensures the economically relevant actor (the one paying tokens and triggering the state change) is the one checked against the allowlist.

---

### Proof of Concept

```
Setup:
  - Pool P is deployed with DepositAllowlistExtension E.
  - Pool admin allowlists address Alice: allowedDepositor[P][Alice] = true.
  - Bob (unauthorized) holds token0 and token1 and has approved LiquidityAdder.

Attack (single tx, Bob as msg.sender):
  1. Bob calls:
       LiquidityAdder.addLiquidityExactShares(
           pool  = P,
           owner = Alice,   // allowlisted address Bob observed on-chain
           salt  = 0,
           deltas = { binIdxs: [0], shares: [1_000_000] },
           maxAmountToken0 = X,
           maxAmountToken1 = Y,
           extensionData = ""
       )

  2. LiquidityAdder calls pool.addLiquidity(Alice, 0, deltas, KIND_PAY, "").

  3. Pool calls E.beforeAddLiquidity(LiquidityAdder, Alice, 0, deltas, "").
     Hook checks allowedDepositor[P][Alice] → true → passes.

  4. Pool mints LP shares to Alice; callback pulls tokens from Bob (payer).

Result:
  - Bob (unauthorized) has deposited into the restricted pool.
  - Alice receives LP shares she did not request.
  - Bob can repeat with different bins to manipulate pool liquidity distribution.
  - The deposit allowlist provided zero protection against Bob.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L88-116)
```text
  function addLiquidityWeighted(
    address pool,
    address owner,
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-196)
```text
  function _addLiquidity(
    address pool,
    address positionOwner,
    uint80 salt,
    LiquidityDelta memory deltas,
    address payer,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
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
