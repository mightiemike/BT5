After thoroughly examining the extension system, hook plumbing, and all production extensions, I found one valid analog.

---

### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` parameter and gates on `owner` (the LP position owner) instead. Any non-allowlisted address can bypass the curated-pool deposit restriction by calling `addLiquidity(owner = <any allowlisted address>, ...)`.

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two actor addresses — `sender` (the actual caller of `addLiquidity`, who provides tokens via the swap callback) and `owner` (the LP position owner). The implementation ignores `sender` entirely and checks `owner`: [1](#0-0) 

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

The pool calls `_beforeAddLiquidity(msg.sender, owner, ...)`, so `sender` = the actual depositor and `owner` = the position beneficiary: [2](#0-1) 

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly gates on `sender` (the actual swapper) and ignores `recipient`: [3](#0-2) 

The asymmetry is the root cause. Because `owner` is a free caller-controlled parameter in `addLiquidity`, any non-allowlisted address can pass an allowlisted address as `owner`, satisfy the check, and inject liquidity into the curated pool. The position is credited to the allowlisted address, but the tokens were provided by the unauthorized caller. [4](#0-3) 

### Impact Explanation
A curated pool deploying `DepositAllowlistExtension` to restrict who may add liquidity has that restriction fully bypassed. Any address can:
1. Inject liquidity into restricted bins, altering the pool's bin distribution and effective pricing.
2. Collude with an allowlisted address (owner) to later `removeLiquidity` and recover the deposited tokens, making the bypass economically free.

This breaks the core pool invariant that only allowlisted depositors may modify LP state on a curated pool, and constitutes a broken core pool functionality / admin-boundary break with direct fund-impacting consequences (unauthorized LP positions, manipulated bin state).

### Likelihood Explanation
Exploitation requires only knowing one allowlisted address (publicly readable from `allowedDepositor`) and calling `addLiquidity` with `owner` set to that address. No special privileges, flash loans, or complex setup are needed. Any user can trigger this on any pool using `DepositAllowlistExtension`.

### Recommendation
Gate on `sender`, not `owner`, consistent with `SwapAllowlistExtension`:

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

### Proof of Concept

```solidity
// Pool has DepositAllowlistExtension; only `alice` is allowlisted.
// Bob (not allowlisted) bypasses the guard:

pool.addLiquidity(
    alice,          // owner — passes allowedDepositor[pool][alice] == true
    salt,
    deltas,
    callbackData,   // Bob's contract provides the tokens here
    extensionData
);
// Result: Bob's tokens are in the pool under alice's position.
// alice.removeLiquidity() recovers them; Bob has deposited into a curated pool
// without ever being allowlisted.
``` [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L12-42)
```text
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }

  function setAllowAllDepositors(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllDepositors[pool_] = allowed;
    emit AllowAllDepositorsSet(pool_, allowed);
  }

  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
  }

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
