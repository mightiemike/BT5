### Title
`DepositAllowlistExtension` checks position `owner` instead of actual depositor `sender`, allowing unprivileged bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking the position `owner` parameter rather than the `sender` (the actual caller of `pool.addLiquidity`). Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` lets any caller specify an arbitrary `owner`, an address that is not on the deposit allowlist can deposit tokens into a restricted pool by nominating an allowlisted address as the position owner. The allowlist guard is bypassed entirely.

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two address parameters from the pool: `sender` (the direct caller of `pool.addLiquidity`) and `owner` (the position owner). The implementation silently drops `sender` and checks only `owner`:

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
``` [1](#0-0) 

The pool encodes both values when it calls the extension:

```solidity
// ExtensionCalling.sol line 95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (the explicit-owner overload) accepts any non-zero `owner` and records `msg.sender` as the token payer:

```solidity
// MetricOmmPoolLiquidityAdder.sol line 56-68
function addLiquidityExactShares(
    address pool, address owner, uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0, uint256 maxAmountToken1,
    bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);   // only checks owner != address(0)
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [3](#0-2) 

`_validateOwner` only rejects `address(0)`: [4](#0-3) 

When `_addLiquidity` calls `pool.addLiquidity(positionOwner, ...)`, the pool's `msg.sender` is the `LiquidityAdder` contract. The pool passes `sender = LiquidityAdder` and `owner = positionOwner` to the extension. The extension checks `allowedDepositor[pool][positionOwner]` — the actual token payer (`msg.sender` of the `LiquidityAdder` call) is never examined. [5](#0-4) 

### Impact Explanation

An address not on the deposit allowlist can deposit tokens into a pool that has `DepositAllowlistExtension` configured as a `beforeAddLiquidity` hook. The attacker supplies an allowlisted address as `owner`; the allowlist check passes; the attacker's tokens are pulled by the callback and credited to the pool; the position is recorded under the allowlisted address. The deposit allowlist — an admin-configured access-control boundary — is bypassed by any unprivileged caller. This qualifies as an admin-boundary break: a pool admin-configured guard is circumvented through an unprivileged periphery path.

Secondary impact: the allowlisted address receives an unsolicited LP position. If that address is a contract without `removeLiquidity` logic, the tokens become permanently locked in the pool under that position key.

### Likelihood Explanation

- No special role or privilege is required; any EOA or contract can call `MetricOmmPoolLiquidityAdder.addLiquidityExactShares`.
- The allowlisted address to nominate as `owner` is public information (readable from `allowedDepositor` mapping).
- The `LiquidityAdder` is a standard periphery contract expected to be used by all depositors.
- The bypass is deterministic and requires no oracle manipulation, flash loan, or timing dependency.

### Recommendation

Change `DepositAllowlistExtension.beforeAddLiquidity` to check the `sender` parameter (the actual direct caller of `pool.addLiquidity`) rather than `owner`:

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

Note that when `MetricOmmPoolLiquidityAdder` is the intermediary, `sender` will be the adder's address, so the adder itself would need to be allowlisted for router-mediated deposits. If per-user gating through the adder is required, the adder must forward the original `msg.sender` via `extensionData` and the extension must decode it — or the pool admin must allowlist the adder and rely on the adder's own access controls.

### Proof of Concept

1. Pool `P` is deployed with `DepositAllowlistExtension` as a `beforeAddLiquidity` hook.
2. Pool admin calls `setAllowedToDeposit(P, Alice, true)`. Alice is the only allowlisted depositor.
3. Bob (not allowlisted) wants to deposit into `P`.
4. Bob calls `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(P, owner=Alice, salt, deltas, max0, max1, "")`.
5. `_addLiquidity` calls `P.addLiquidity(Alice, salt, deltas, abi.encode(KIND_PAY), "")`.
6. Pool calls `DepositAllowlistExtension.beforeAddLiquidity(sender=LiquidityAdder, owner=Alice, ...)`.
7. Extension evaluates `allowedDepositor[P][Alice]` → `true` → no revert.
8. Pool proceeds; callback fires; Bob's tokens are pulled via `pay(token, Bob, P, amount)`.
9. Position is recorded under `(Alice, salt)`. Bob's tokens are now in the pool. The deposit allowlist did not block Bob. [1](#0-0) [3](#0-2) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-207)
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
    ) {
      amount0Added = a0;
      amount1Added = a1;
      _clearPayContext();
    } catch (bytes memory reason) {
      _clearPayContext();
      assembly ("memory-safe") {
        revert(add(reason, 32), mload(reason))
      }
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
