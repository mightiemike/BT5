### Title
Deposit Allowlist Checks `owner` Instead of `sender`, Allowing Any Unauthorized Address to Bypass the Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the address that actually calls `pool.addLiquidity` and pays tokens via callback) and gates only on `owner` (the position-owner address that receives LP shares). Because `owner` is a free caller-supplied parameter, any unprivileged address can pass the allowlist check by nominating an already-allowed `owner`, depositing tokens into a restricted pool without being on the allowlist itself.

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` parameter and passes both `msg.sender` (as `sender`) and `owner` to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both values: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but leaves it unnamed and unused. The allowlist lookup is performed exclusively against `owner`: [3](#0-2) 

`owner` is a free parameter supplied by the caller of `pool.addLiquidity`. There is no constraint that `msg.sender == owner`. Any address can therefore call:

```solidity
pool.addLiquidity(
    allowedOwner,   // any address already on the allowlist
    salt,
    deltas,
    callbackData,
    extensionData
);
```

The extension evaluates `allowedDepositor[pool][allowedOwner]` → `true` and returns without reverting. The pool then issues the `metricOmmAddLiquidityCallback` to `msg.sender` (the unauthorized caller), who pays the tokens, and mints LP shares to `allowedOwner`.

The project's own audit-target commentary confirms the intended invariant: [4](#0-3) 

### Impact Explanation

The deposit allowlist is the sole on-chain mechanism for restricting who may provide liquidity to a pool. With this bug the guard is entirely ineffective: any address, regardless of allowlist status, can deposit tokens into a restricted pool by nominating an already-allowed `owner`. Consequences include:

- **Allowlist invariant fully broken**: unauthorized actors can supply liquidity to pools intended to be KYC-gated, compliance-restricted, or otherwise closed.
- **Forced LP position on allowed owner**: the allowed owner receives shares they did not initiate; while they can remove them, this constitutes unsolicited state mutation on their account.
- **Pool liquidity manipulation**: the unauthorized depositor controls which bins receive liquidity, potentially skewing the pool's depth profile and affecting swap pricing for all users.

This satisfies the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" criterion.

### Likelihood Explanation

Exploitation requires only a direct call to `pool.addLiquidity` with a known allowed-owner address. No special privileges, flash loans, or oracle manipulation are needed. The allowed-owner address is discoverable from on-chain events (`AllowedToDepositSet`). Likelihood is **High**.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L188-195)
```text
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
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

**File:** generate_scanned_questions.py (L647-654)
```python
            short="deposit allowlist gate",
            file_function="metric-periphery/contracts/extensions/DepositAllowlistExtension.sol::beforeAddLiquidity",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::addLiquidity and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*",
            call_path="public liquidity flow -> beforeAddLiquidity hook -> allowAll/allowedDepositor lookup keyed by pool and owner",
            values="the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares",
            control_hint="The attacker can separate payer from owner and can route through the liquidity adder, so the checked identity has to be exactly the one the pool intends to gate.",
            validation_focus="Exercise direct pool adds and liquidity-adder adds with mismatched owner/payer pairs and assert the allowlist always gates the economically relevant depositor.",
        ),
```
