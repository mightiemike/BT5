Audit Report

## Title
`DepositAllowlistExtension` Gates on `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead gates on `owner` (the LP share beneficiary). Because `MetricOmmPool.addLiquidity` imposes no restriction on who may supply the `owner` parameter, any unprivileged caller can name an allowlisted address as `owner`, pass the hook check, pay tokens via the callback, and have LP shares minted to that address — fully circumventing the pool admin's deposit allowlist.

## Finding Description

`MetricOmmPool.addLiquidity` invokes the extension hook with both `msg.sender` and the caller-supplied `owner`:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully encodes both arguments:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

Inside `DepositAllowlistExtension`, the first positional argument (`sender`) is unnamed and discarded; the guard reads only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

`addLiquidity` has no restriction on who may supply `owner` — the only access-control gate is the extension hook itself: [4](#0-3) 

The sister extension `SwapAllowlistExtension` demonstrates the correct pattern — it names and checks `sender`, not `recipient`: [5](#0-4) 

**Exploit path:**
1. Pool is configured with `DepositAllowlistExtension`; `allowedDepositor[pool][alice] = true`, `allowedDepositor[pool][bob] = false`.
2. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
3. The hook evaluates `allowedDepositor[pool][alice]` → `true`; no revert.
4. `LiquidityLib.addLiquidity` executes; Bob's callback (`metricOmmAddLiquidityCallback`) pays the tokens into the pool.
5. LP shares are minted to Alice; Bob has deposited into an allowlist-gated pool without being on the allowlist.

No existing guard prevents this: `addLiquidity` has no `owner`-restriction check, and the reentrancy guard is orthogonal to access control.

## Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may inject tokens (e.g., KYC/AML, curated LP sets). With the guard checking `owner` instead of `sender`, any unprivileged actor can deposit real tokens into an allowlist-gated pool, fully bypassing the admin-configured access control. This constitutes a direct admin-boundary break: an unprivileged path bypasses a factory-initialized, pool-admin-controlled guard. The unauthorized depositor's tokens enter the pool's bin accounting (`binTotals`), and LP shares are minted to the named `owner`. Additionally, an attacker can inflate bin balances before a swap, potentially affecting oracle-derived metrics read by extensions such as `OracleValueStopLossExtension`.

## Likelihood Explanation

Exploitation requires only a standard `addLiquidity` call with any allowlisted address as `owner`. No special permissions, flash loans, oracle manipulation, or privileged roles are needed. Any on-chain actor can execute this in a single transaction, making it trivially repeatable.

## Recommendation

Replace the unnamed first parameter with `sender` and gate on it, mirroring `SwapAllowlistExtension`:

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

If the intent is to gate on the position owner rather than the payer, the contract name, NatSpec, `setAllowedToDeposit` semantics, and the security model must be re-evaluated and documented accordingly.

## Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  allowedDepositor[pool][bob]   = false  // bob is NOT allowed

Attack (single tx, no special role):
  bob calls pool.addLiquidity(
      owner    = alice,   // allowlisted address
      salt     = 0,
      deltas   = <valid bin deltas>,
      callbackData = <bob pays tokens in callback>,
      extensionData = ""
  )

Result:
  hook checks allowedDepositor[pool][alice] == true → passes
  bob's callback transfers tokens to pool
  LP shares minted to alice
  bob has deposited into an allowlist-gated pool without being allowlisted
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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
