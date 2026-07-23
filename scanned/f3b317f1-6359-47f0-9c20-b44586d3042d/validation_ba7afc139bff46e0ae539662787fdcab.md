### Title
`DepositAllowlistExtension` gates LP position `owner` instead of token payer `sender`, allowing any non-allowlisted address to bypass the deposit guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument (the actual token payer / `msg.sender` of `addLiquidity`) and checks only the caller-supplied `owner` (LP position owner) against the allowlist. Because `owner` is a free parameter accepted from any caller, a non-allowlisted address can pass an allowlisted address as `owner`, satisfy the guard, and deposit tokens into the pool — breaking the invariant the extension is designed to enforce.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address from its caller and forwards both `msg.sender` (as `sender`) and `owner` to the extension dispatcher: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes both values and calls the configured extension: [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first positional parameter (`sender`) is unnamed and **completely ignored**. The guard checks only `owner`: [3](#0-2) 

Because `owner` is caller-supplied with no binding to the actual payer, the check `allowedDepositor[msg.sender][owner]` passes whenever `owner` is an allowlisted address — regardless of who is actually calling `addLiquidity` and paying the tokens.

The same bypass is reachable through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)`, which accepts an arbitrary `owner` and only validates it is non-zero: [4](#0-3) [5](#0-4) 

The adder stores `msg.sender` as the payer in transient context and calls `pool.addLiquidity(positionOwner, ...)`. The extension sees `owner = allowlisted_address` and passes; tokens are pulled from the non-allowlisted caller; LP shares are minted to `allowlisted_address`.

The contract's own NatSpec confirms the intent is to gate the depositor, not the position owner: [6](#0-5) 

---

### Impact Explanation

The deposit allowlist invariant is fully broken. Any non-allowlisted address can deposit tokens into a guarded pool by nominating any allowlisted address as `owner`. The LP shares land with the allowlisted address (who can then withdraw them), while the non-allowlisted address has successfully injected tokens into a pool that was supposed to reject them. Pools deployed with this extension for regulatory gating, guarded-launch access control, or KYC-restricted liquidity provision silently accept deposits from the entire public.

---

### Likelihood Explanation

Exploitation requires no special privilege. Any externally-owned account can call `pool.addLiquidity` or `liquidityAdder.addLiquidityExactShares` directly, supply any known allowlisted address as `owner`, and the guard passes unconditionally. The allowlisted address is typically discoverable on-chain from prior `setAllowedToDeposit` events.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual caller / token payer) rather than `owner`:

```solidity
// current — wrong identity checked
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}

// fixed — gate the actual payer
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

If the intent is to gate both the payer and the position owner, both should be checked.

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][ALICE] = true   // ALICE is allowlisted
  BOB is NOT allowlisted

Attack (direct pool call):
  BOB calls pool.addLiquidity(
      owner = ALICE,   // allowlisted address supplied as owner
      salt  = 0,
      deltas = <valid bins>,
      callbackData = ...,
      extensionData = ...
  )

Extension check:
  beforeAddLiquidity(sender=BOB

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
