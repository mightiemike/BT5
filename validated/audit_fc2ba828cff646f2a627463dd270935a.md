### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual depositor) and gates on `owner` (the position owner). Any unprivileged caller can bypass the allowlist by naming an already-allowlisted address as `owner`.

---

### Finding Description

The contract is documented as *"Gates `addLiquidity` by depositor address, per pool"* and its admin setter is named `setAllowedToDeposit(pool, depositor, allowed)`. The parallel `SwapAllowlistExtension` correctly checks `sender` (the actual caller). However, `DepositAllowlistExtension.beforeAddLiquidity` drops the first parameter and checks `owner` instead:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The pool passes both arguments correctly:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

And `ExtensionCalling` forwards them faithfully:

```solidity
// ExtensionCalling.sol L95-98
_callExtensionsInOrder(
    BEFORE_ADD_LIQUIDITY_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
);
``` [3](#0-2) 

Because `owner` is a free caller-supplied argument (not authenticated by the pool), any non-allowlisted address can pass an allowlisted address as `owner` and the guard approves the call.

The `MetricOmmPoolLiquidityAdder` makes this trivially exploitable for EOAs: the caller is the `payer` (tokens are pulled from them) while `owner` is whatever address they supply:

```solidity
// MetricOmmPoolLiquidityAdder.sol L56-68
function addLiquidityExactShares(
    address pool,
    address owner,          // ← attacker sets this to an allowlisted address
    ...
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...); // msg.sender is payer
}
``` [4](#0-3) 

---

### Impact Explanation

The deposit allowlist is an admin-boundary control. Its bypass lets non-allowlisted actors inject liquidity into restricted pools. In the collusion path (non-allowlisted depositor + allowlisted `owner` cooperate), the non-allowlisted party effectively earns LP fees from a pool they are explicitly barred from. In the griefing path, a non-allowlisted actor can force-deposit tokens into any allowlisted address's position without consent, polluting that LP's position accounting. Both paths violate the pool admin's access-control invariant.

---

### Likelihood Explanation

Exploitation requires only a valid allowlisted address to name as `owner` — a piece of public on-chain information (any existing allowlisted LP). No privileged role, flash loan, or oracle manipulation is needed. The `MetricOmmPoolLiquidityAdder` router is the standard entry point and requires no special setup.

---

### Recommendation

Check `sender` (the actual depositor) instead of `owner`, mirroring `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [5](#0-4) 

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension`; `alice` is allowlisted, `bob` is not.
2. `bob` (EOA) calls:
   ```
   MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, extData)
   ```
3. Router calls `pool.addLiquidity(alice, salt, deltas, abi.encode(KIND_PAY), extData)`.
4. Pool calls `_beforeAddLiquidity(router, alice, ...)`.
5. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. Pool executes liquidity addition; callback pulls tokens from `bob` (the payer).
7. Position is recorded under `alice`; `alice` later calls `removeLiquidity` and returns proceeds to `bob`.
8. `bob` has effectively provided liquidity to a pool that explicitly excluded him.

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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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
