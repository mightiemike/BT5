The code confirms all claims in the report. Let me verify the key facts:

1. `DepositAllowlistExtension.beforeAddLiquidity` ignores the first `address` parameter (sender) and checks only `owner`: [1](#0-0) 

2. `MetricOmmPool.addLiquidity` passes `msg.sender` (the LiquidityAdder) as sender and the caller-supplied `owner` as owner to the extension: [2](#0-1) 

3. `addLiquidityExactShares` accepts an arbitrary `owner` with only a non-zero check, while the actual payer is `msg.sender`: [3](#0-2) 

4. `_validateOwner` only checks for zero address: [4](#0-3) 

5. The payer is stored separately in transient context, completely decoupled from `owner`: [5](#0-4) 

All code references check out. The exploit path is deterministic and requires no special privileges.

---

Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of Actual Payer, Allowing Unauthorized Deposit Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking the `owner` (position holder) argument rather than the actual token payer. Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` allows any caller to supply an arbitrary `owner` address while paying tokens themselves, an unauthorized actor can bypass the deposit allowlist by naming any allowlisted address as `owner`. The pool admin's deposit restriction is fully circumvented.

## Finding Description
`DepositAllowlistExtension.beforeAddLiquidity` ignores the `sender` parameter (first `address` argument, unnamed) and checks only `allowedDepositor[msg.sender][owner]`, where `msg.sender` is the pool and `owner` is the position holder supplied by the caller:

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

`MetricOmmPool.addLiquidity` passes `msg.sender` (the LiquidityAdder contract) as `sender` and the caller-supplied `owner` as `owner` to the extension hook. `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts an arbitrary `owner` from the caller and only validates it is non-zero via `_validateOwner`. The actual payer is always `msg.sender` of the `addLiquidityExactShares` call, stored separately in transient context via `_setPayContext`. There is no requirement that `owner == msg.sender`. An unauthorized actor (Bob) can name any allowlisted address (Alice) as `owner`, causing the extension to check `allowedDepositor[pool][alice] == true` and pass, while Bob pays the tokens and Alice receives the LP shares.

## Impact Explanation
The deposit allowlist is a core access control mechanism for pool admins to restrict who may add liquidity (e.g., KYC/compliance-gated pools). The bypass is complete: any unprivileged actor can add liquidity to a restricted pool by naming an allowlisted address as `owner`. This constitutes broken core pool functionality — the deposit allowlist guard is reachable but misapplied, producing a state the pool admin explicitly intended to prevent. Unauthorized actors can influence pool composition and bin state, affect price discovery, and grief existing LPs by adding liquidity at adversarially chosen bin positions.

## Likelihood Explanation
No special privileges are required; any EOA or contract can call `addLiquidityExactShares` with an arbitrary `owner`. Allowlisted addresses are publicly discoverable from `AllowedToDepositSet` on-chain events. The LiquidityAdder is a standard periphery contract intended for general use. The bypass is deterministic and requires no timing, oracle manipulation, or flash loans.

## Recommendation
`DepositAllowlistExtension.beforeAddLiquidity` should gate the actual depositing actor rather than the position holder. Two options:

1. **Check `sender` instead of `owner`**: The extension gates the address that called `pool.addLiquidity` (i.e., the LiquidityAdder or direct depositor). Pool admins allowlist the LiquidityAdder for router-mediated deposits and individual EOAs for direct deposits.

2. **Enforce `owner == msg.sender` in `addLiquidityExactShares`**: Restrict the explicit-owner overload so the caller can only deposit into their own position, making `sender == owner` always true for the LiquidityAdder path. This is simpler and closes the bypass without changing extension semantics.

## Proof of Concept
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
  2. _addLiquidity(pool, alice, salt, deltas, msg.sender=Bob, ...)
  3. _setPayContext(pool, Bob, max0, max1)
  4. pool.addLiquidity(alice, salt, deltas, abi.encode(KIND_PAY), "")
       msg.sender in pool = LiquidityAdder
  5. _beforeAddLiquidity(LiquidityAdder, alice, ...)
  6. DepositAllowlistExtension.beforeAddLiquidity(LiquidityAdder, alice, ...)
       allowedDepositor[pool][alice] == true  → NO REVERT
  7. LiquidityLib.addLiquidity mints shares to alice
  8. Callback pulls tokens from Bob (payer stored in transient context)

Result:
  Bob (unauthorized) has added liquidity to the restricted pool.
  Alice holds the LP shares; Bob paid the tokens.
  The deposit allowlist is bypassed.
```

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
