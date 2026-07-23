### Title
`DepositAllowlistExtension` Checks Caller-Supplied `owner` Instead of Actual `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` enforces the deposit allowlist against the caller-supplied `owner` parameter rather than the actual transaction initiator (`sender`). Any unprivileged address can bypass the guard by passing an allowlisted address as `owner`, depositing tokens into the pool while the allowlist check silently passes.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook with two distinct addresses:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`msg.sender` is the real depositor; `owner` is a free caller-supplied parameter that determines who owns the resulting position.

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first argument (`sender`) is silently discarded and the check is performed on the second argument (`owner`):

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

Because `owner` is fully attacker-controlled, any caller can pass an allowlisted address as `owner`, satisfy the check, and have their deposit accepted. The pool then records the position under `owner` (the allowlisted address) while the actual token transfer is executed by the real caller via the swap callback.

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual caller):

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

The asymmetry between the two sibling extensions confirms the deposit check is checking the wrong field.

---

### Impact Explanation

The deposit allowlist is the primary access-control mechanism for restricting who may provide liquidity to a pool. Bypassing it means:

1. Any address — regardless of allowlist status — can inject liquidity into a restricted pool.
2. The resulting position is credited to the allowlisted `owner`, not the attacker, so the attacker forfeits the deposited tokens. However, the pool's liquidity composition is altered without authorization, which can shift bin balances, affect oracle-anchored swap pricing, and trigger or suppress the `OracleValueStopLossExtension` watermarks for bins the attacker selects.
3. Pools deployed for permissioned environments (e.g., institutional or compliance-gated pools) have their core access control silently nullified.

This constitutes broken core pool functionality: the allowlist guard does not protect the invariant it is designed to enforce.

---

### Likelihood Explanation

Exploitation requires no special privilege. Any externally-owned account or contract can call `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)`. The allowlisted address need only be any address already present in `allowedDepositor[pool]`, which is public state readable on-chain. No flash loan, oracle manipulation, or admin cooperation is required.

---

### Recommendation

Replace the ignored first parameter with a named `sender` and enforce the allowlist against it, mirroring the correct pattern in `SwapAllowlistExtension`:

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

---

### Proof of Concept

1. Pool `P` is deployed with `DepositAllowlistExtension` as a `beforeAddLiquidity` hook.
2. Admin calls `setAllowedToDeposit(P, alice, true)`. Alice is the only allowlisted depositor.
3. Bob (not allowlisted) constructs a contract `BobRouter` that implements `IMetricOmmSwapCallback`.
4. `BobRouter` calls `P.addLiquidity(alice, 0, deltas, callbackData, "")`.
5. The extension evaluates `allowedDepositor[P][alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` records the position under key `(alice, 0)`.
7. The pool calls `BobRouter.metricOmmSwapCallback(...)`, which transfers the required tokens.
8. Bob has deposited into the restricted pool; the allowlist check was never applied to Bob. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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
