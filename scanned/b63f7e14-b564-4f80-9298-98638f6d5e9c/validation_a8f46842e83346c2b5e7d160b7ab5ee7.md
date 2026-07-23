### Title
`DepositAllowlistExtension` Checks Wrong Actor (`owner` Instead of `sender`), Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` is documented as gating `addLiquidity` **by depositor address**. However, it silently discards the `sender` argument (the actual payer/caller) and instead checks `owner` (the position recipient). Because `addLiquidity` explicitly supports an operator pattern where `msg.sender ≠ owner`, any address not on the allowlist can bypass the gate by calling `pool.addLiquidity(allowedAddress, ...)` and routing the LP position to an allowed owner.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct actors to the extension:

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
//                  ^^^^^^^^^^  ^^^^^
//                  sender      owner (position recipient)
``` [1](#0-0) 

The NatDoc for `addLiquidity` explicitly states: *"`msg.sender` pays but need not equal `owner` (operator pattern)."* [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` ignores `sender` entirely (unnamed `address,`) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

The contract's own NatDoc says it *"Gates `addLiquidity` by depositor address"* — the depositor is the payer (`sender`), not the position recipient (`owner`). [4](#0-3) 

---

### Impact Explanation

A pool admin deploys a curated pool with `DepositAllowlistExtension` to restrict which addresses can deposit tokens. Because the check is on `owner` rather than `sender`, any address not on the allowlist can:

1. Call `pool.addLiquidity(allowedAddress, salt, deltas, callbackData, extensionData)` directly.
2. The extension evaluates `allowedDepositor[pool][allowedAddress]` → `true` → passes.
3. The unauthorized caller (`sender`) pays the tokens and alters pool state (bin totals, cursor position, price impact).
4. LP shares are credited to `allowedAddress`.

The pool admin's curation policy — preventing specific addresses from depositing — is completely defeated. The unauthorized depositor changes pool composition and can influence oracle-anchored pricing for subsequent swaps, constituting a broken core pool functionality and allowlist bypass with direct fund-impact potential on LP positions.

---

### Likelihood Explanation

- The operator pattern (`msg.sender ≠ owner`) is explicitly documented and supported by the pool interface.
- `MetricOmmPoolLiquidityAdder` also uses this pattern: `addLiquidityExactShares(pool, owner, ...)` passes `msg.sender` as payer and a caller-supplied `owner` as position recipient.
- Any address can call `pool.addLiquidity` directly with an arbitrary `owner`.
- No special privilege or setup is required beyond knowing one allowed address (which is public via `allowedDepositor` mapping). [5](#0-4) 

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual payer/depositor) instead of `owner`:

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

This aligns the check with the actor who actually pays tokens and drives pool state changes.

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  bob is NOT on the allowlist

Attack:
  bob calls pool.addLiquidity(
      owner = alice,   // allowed address
      salt  = 0,
      deltas = <bob's desired bins/shares>,
      callbackData = <bob's payment callback>,
      extensionData = ""
  )

Extension evaluation:
  sender (ignored) = bob
  owner (checked)  = alice
  allowedDepositor[pool][alice] == true  →  check passes

Result:
  bob pays tokens into the pool (pool state changes)
  alice receives LP shares she did not request
  bob has bypassed the deposit allowlist entirely
``` [3](#0-2) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L147-147)
```text
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-11)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-98)
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
```
