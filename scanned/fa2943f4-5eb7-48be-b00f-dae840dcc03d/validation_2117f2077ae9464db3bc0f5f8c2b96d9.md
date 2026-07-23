After reading the key extension files, pool contract, and payment infrastructure, I found a concrete analog.

---

### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit guard — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking the `owner` parameter (the LP-position beneficiary) rather than the `sender` parameter (the actual caller who provides funds via callback). Because `addLiquidity` accepts an arbitrary `owner` address supplied by the caller, any address not on the allowlist can bypass the guard by passing any allowlisted address as `owner`.

### Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as the LP-position recipient to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both values faithfully: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` then ignores `sender` (first positional argument, named `address` and discarded with `,`) and checks only `owner`: [3](#0-2) 

The NatSpec on the contract explicitly states the intent is to gate by **depositor** address: [4](#0-3) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swapper), demonstrating the intended pattern: [5](#0-4) 

Because `addLiquidity` imposes no restriction on who may supply the `owner` argument (unlike `removeLiquidity`, which enforces `msg.sender == owner`), any caller can pass any allowlisted address as `owner` and the guard passes unconditionally. [6](#0-5) 

### Impact Explanation

An unprivileged address (not on the allowlist) can:

1. Call `pool.addLiquidity(allowlistedOwner, salt, deltas, callbackData, extensionData)`.
2. The extension checks `allowedDepositor[pool][allowlistedOwner]` → `true` → guard passes.
3. The attacker provides funds via the modify-liquidity callback; the allowlisted `owner` receives LP shares they did not initiate.
4. The attacker has now provided liquidity to a restricted pool, earning ongoing fee accrual from a pool the admin intended to keep closed.

Additionally, if the allowlisted `owner` is a contract that cannot call `removeLiquidity` (which requires `msg.sender == owner`), the deposited principal is permanently locked in the LP position — a direct loss of the attacker's own funds and an unremovable position for the owner. This mirrors the external report's pattern: a fallback path (here, using `owner` as the checked identity) routes value to a recipient that cannot properly handle it.

### Likelihood Explanation

Exploitation requires only knowing one allowlisted address (trivially discoverable from on-chain events or the public `allowedDepositor` mapping) and calling `addLiquidity` with that address as `owner`. No special permissions, flash loans, or oracle manipulation are needed. Any pool deploying `DepositAllowlistExtension` is affected from the moment of deployment.

### Recommendation

Replace the `owner` check with `sender` in `beforeAddLiquidity`, consistent with how `SwapAllowlistExtension` handles `beforeSwap`:

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

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  bob is NOT on the allowlist

Attack:
  bob calls pool.addLiquidity(
      owner = alice,   // allowlisted — guard passes
      salt  = 0,
      deltas = <valid bins>,
      callbackData = "",
      extensionData = ""
  )

Extension check:
  allowedDepositor[pool][alice] == true  →  no revert

Result:
  bob's funds are pulled via callback
  alice receives LP shares she never requested
  bob earns fee accrual from a pool the admin restricted
  if alice is a contract without removeLiquidity support,
  the deposited tokens are permanently locked
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-12)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
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
