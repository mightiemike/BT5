### Title
`DepositAllowlistExtension` Checks `owner` Instead of Actual Payer, Allowing Unauthorized Deposit Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking the `owner` (position holder) argument rather than the actual token payer. Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` lets any caller supply an arbitrary `owner` address while paying tokens themselves, an unauthorized actor can bypass the deposit allowlist by naming any allowlisted address as `owner`.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` ignores the `sender` parameter and checks only `owner`: [1](#0-0) 

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The pool passes `msg.sender` (the LiquidityAdder contract) as `sender` and the caller-supplied `owner` as `owner` to the extension: [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts an arbitrary `owner` from the caller and only validates it is non-zero: [3](#0-2) 

```solidity
function addLiquidityExactShares(
    address pool, address owner, uint80 salt, LiquidityDelta calldata deltas,
    uint256 maxAmountToken0, uint256 maxAmountToken1, bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);   // only checks owner != address(0)
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [4](#0-3) 

The `_validateOwner` check: [4](#0-3) 

```solidity
function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
}
```

There is no requirement that `owner == msg.sender`. The actual payer is always `msg.sender` of the `addLiquidityExactShares` call, stored separately in transient context: [5](#0-4) 

The extension's contract-level comment states it "Gates `addLiquidity` by depositor address" — meaning the depositing actor — but the implementation gates by position holder (`owner`), which is a different address when the LiquidityAdder is used. [6](#0-5) 

---

### Impact Explanation

An unauthorized actor (Bob, not on the allowlist) can add liquidity to a restricted pool by:
1. Discovering any allowlisted address (Alice) from on-chain events (`AllowedToDepositSet`).
2. Calling `addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, extensionData)`.
3. The extension checks `allowedDepositor[pool][alice]` → `true` → passes.
4. Bob pays the tokens; Alice receives the LP shares.

The pool admin's deposit restriction is fully bypassed. Unauthorized liquidity addition can:
- Violate the pool's intended access control (e.g., KYC/compliance-gated pools).
- Allow unauthorized actors to influence pool composition and bin state.
- Enable griefing by adding liquidity at adversarially chosen bin positions, affecting price discovery and LP returns for existing holders.

This matches the **broken core pool functionality** impact gate: the deposit allowlist guard is reachable but misapplied, producing a state the pool admin explicitly intended to prevent.

---

### Likelihood Explanation

- No special privileges are required; any EOA or contract can call `addLiquidityExactShares` with an arbitrary `owner`.
- Allowlisted addresses are publicly discoverable from `AllowedToDepositSet` events.
- The LiquidityAdder is a standard periphery contract intended for general use.
- The bypass is deterministic and requires no timing or oracle manipulation.

---

### Recommendation

`DepositAllowlistExtension.beforeAddLiquidity` should check `sender` (the actual caller of `addLiquidity` on the pool, i.e., the LiquidityAdder or direct depositor) rather than `owner`. However, since `sender` is the LiquidityAdder when routed through it, the correct fix is to check the **actual payer**. Two options:

1. **Check `sender` instead of `owner`**: The extension gates the address that called `pool.addLiquidity`. Pool admins allowlist the LiquidityAdder for router-mediated deposits and individual EOAs for direct deposits.

2. **Enforce `owner == msg.sender` in `addLiquidityExactShares`**: Restrict the explicit-owner overload so the caller can only deposit into their own position, making `sender == owner` always true for the LiquidityAdder path.

Option 2 is simpler and closes the bypass without changing extension semantics.

---

### Proof of Concept

```
Setup:
  - Pool deployed with DepositAllowlistExtension
  - allowedDepositor[pool][alice] = true
  - Bob is NOT allowlisted

Attack:
  Bob calls:
    LiquidityAdder.addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, "")

Execution trace:
  1. _validateOwner(alice)  → passes (alice != address(0))
  2. pool.addLiquidity(alice, salt, deltas, KIND_PAY, "")
       msg.sender = LiquidityAdder
  3. _beforeAddLiquidity(LiquidityAdder, alice, ...)
  4. DepositAllowlistExtension.beforeAddLiquidity(LiquidityAdder, alice, ...)
       allowedDepositor[pool][alice] == true  → NO REVERT
  5. LiquidityLib.addLiquidity mints shares to alice
  6. Callback pulls tokens from Bob (payer = msg.sender of step 1)

Result:
  Bob (unauthorized) has added liquidity to the restricted pool.
  Alice holds the LP shares; Bob paid the tokens.
  The deposit allowlist is bypassed.
```

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L192-196)
```text
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
