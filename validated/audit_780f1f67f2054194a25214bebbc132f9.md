### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any unprivileged actor to bypass the deposit allowlist - (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` by **depositor address**, but its `beforeAddLiquidity` hook silently discards the `sender` parameter and checks `owner` instead. Any unprivileged actor can bypass the allowlist by calling `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` with an allowlisted address as `owner`, causing their tokens to enter a restricted pool and be permanently credited to the allowlisted owner's position.

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct actors to the extension hook:

- `sender` = `msg.sender` of `addLiquidity` — the actual depositor who pays tokens via the callback
- `owner` = the position owner whose shares are credited [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently drops `sender` (unnamed first parameter) and checks only `owner`: [3](#0-2) 

The NatSpec and storage variable name both declare the intent is to gate by **depositor** address: [4](#0-3) 

The bypass path through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts a caller-supplied `owner`: [5](#0-4) 

When the `LiquidityAdder` calls `pool.addLiquidity(allowlistedOwner, ...)`, the pool passes `sender = LiquidityAdder` and `owner = allowlistedOwner` to the extension. The extension checks `allowedDepositor[pool][allowlistedOwner]` → true → passes. The callback then pulls tokens from the unauthorized user (stored as `payer` in transient context): [6](#0-5) 

Because `removeLiquidity` enforces `msg.sender == owner`, the unauthorized depositor's tokens are permanently locked in the allowlisted owner's position and cannot be recovered by the depositor: [7](#0-6) 

### Impact Explanation

The deposit allowlist is completely bypassed. Any unprivileged actor can deposit tokens into a restricted pool by supplying an allowlisted address as `owner`. The depositor's tokens are permanently credited to the allowlisted owner's position (which only the allowlisted owner can withdraw), constituting an irreversible loss of the depositor's principal. Pools relying on this extension for regulatory compliance (KYC gating) or access control have that control fully negated.

### Likelihood Explanation

The bypass requires only knowledge of one allowlisted address (publicly readable from `allowedDepositor`) and a call to the deployed `MetricOmmPoolLiquidityAdder`. No privileged access, flash loan, or special setup is needed. Any actor can trigger this against any pool using `DepositAllowlistExtension` with `allowAllDepositors = false`.

### Recommendation

Replace the `owner` check with a `sender` check in `beforeAddLiquidity`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This aligns the implementation with the documented intent ("Gates `addLiquidity` by depositor address") and the storage variable name `allowedDepositor`.

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured; `allowAllDepositors[pool] = false`; only `ALICE` is in `allowedDepositor[pool]`.
2. Unauthorized `BOB` calls:
   ```solidity
   liquidityAdder.addLiquidityExactShares(
       pool,
       ALICE,   // owner = allowlisted address
       salt,
       deltas,
       maxAmount0,
       maxAmount1,
       extensionData
   );
   ```
3. `LiquidityAdder` calls `pool.addLiquidity(ALICE, salt, deltas, abi.encode(KIND_PAY), extensionData)`.
4. Pool calls `_beforeAddLiquidity(LiquidityAdder, ALICE, ...)`.
5. Extension evaluates `allowedDepositor[pool][ALICE]` → `true` → no revert.
6. Liquidity is added; callback pulls tokens from `BOB`.
7. `BOB`'s tokens are now in `ALICE`'s position. `BOB` cannot call `removeLiquidity` (not the owner). The allowlist was bypassed; `BOB`'s principal is lost.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-13)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-178)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }

    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
    _clearPayContext();
```
