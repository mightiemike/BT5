### Title
`DepositAllowlistExtension` gates `owner` instead of `sender`, allowing non-allowlisted depositors to bypass the curated-pool deposit guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` checks the `owner` (position recipient) parameter against the per-pool allowlist, but ignores `sender` (the actual caller who pays tokens). Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` lets any caller supply an arbitrary `owner`, a non-allowlisted address can deposit its own tokens into a curated pool by nominating any allowlisted address as the position owner. The allowlist guard is silently bypassed.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` is:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The first parameter (`sender`, the `msg.sender` of the pool's `addLiquidity` call) is silently discarded. Only `owner` is checked.

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner`:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (the explicit-owner overload) accepts any non-zero `owner` from the caller and sets the payer to `msg.sender`:

```solidity
function addLiquidityExactShares(
    address pool, address owner, uint80 salt, LiquidityDelta calldata deltas,
    uint256 maxAmountToken0, uint256 maxAmountToken1, bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);   // only checks owner != address(0)
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [3](#0-2) 

`_validateOwner` only rejects `address(0)`: [4](#0-3) 

Inside `_addLiquidity`, the adder calls `pool.addLiquidity(positionOwner, ...)`, making `msg.sender` in the pool = the adder contract, and `owner` = the attacker-chosen address: [5](#0-4) 

The extension then evaluates `allowedDepositor[pool][owner]` — the allowlisted address chosen by the attacker — and passes.

---

### Impact Explanation

A non-allowlisted address (Bob) can deposit its own tokens into a curated pool that has `DepositAllowlistExtension` active by calling `addLiquidityExactShares(pool, alice, ...)` where `alice` is any allowlisted address. Bob's tokens are pulled from Bob (he is the payer stored in transient context), the LP position is minted to Alice, and the extension never sees Bob's address. The pool admin's deposit curation policy — the only on-chain mechanism restricting who may provide liquidity — is silently voided. Any non-allowlisted actor can inject capital into the pool, alter bin balances, and affect the pool's price cursor and LP accounting, all of which have direct fund-level consequences for existing LPs and the pool's solvency invariants.

---

### Likelihood Explanation

The `MetricOmmPoolLiquidityAdder` is the standard periphery entry point for liquidity provision and is publicly callable by anyone. No privileged role, special token, or unusual setup is required. The attacker only needs to know one allowlisted address (readable from `allowedDepositor` events or the public mapping) and have tokens to deposit. The bypass is reachable on every curated pool that uses `DepositAllowlistExtension` with the `beforeAddLiquidity` hook enabled.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual caller who pays tokens) rather than — or in addition to — `owner`. Because `sender` is the adder contract when users route through periphery, the extension should also accept the adder as a trusted forwarder only when the adder itself enforces the allowlist, or the pool admin should allowlist the adder and rely on a separate per-user check. The minimal safe fix is:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender]
        && !allowedDepositor[msg.sender][sender]   // gate the actual caller/payer
        && !allowedDepositor[msg.sender][owner]) { // optionally also gate the owner
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Pool admins must then decide whether to allowlist the adder contract (open to all adder users) or require direct pool calls for curated pools.

---

### Proof of Concept

```
Setup:
  pool  = MetricOmmPool with DepositAllowlistExtension (beforeAddLiquidity hook)
  alice = allowlisted depositor  (allowedDepositor[pool][alice] = true)
  bob   = NOT allowlisted

Attack:
  1. bob calls:
       adder.addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, extensionData)
     msg.sender = bob → payer = bob (stored in transient T_SLOT_PAY_PAYER)
     owner = alice (passed through to pool.addLiquidity)

  2. adder calls:
       pool.addLiquidity(alice, salt, deltas, abi.encode(KIND_PAY), extensionData)
     pool sees: msg.sender = adder, owner = alice

  3. pool calls:
       extension.beforeAddLiquidity(adder, alice, salt, deltas, extensionData)
     extension evaluates: allowedDepositor[pool][alice] == true → NO REVERT

  4. pool mints LP shares to alice; callback pulls tokens from bob.

Result:
  - bob (non-allowlisted) successfully deposited tokens into the curated pool.
  - The DepositAllowlistExtension never evaluated bob's address.
  - alice receives LP shares she did not initiate.
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L194-196)
```text
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
