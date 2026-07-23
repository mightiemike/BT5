### Title
`DepositAllowlistExtension` Checks Caller-Supplied `owner` Instead of Actual Caller `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the caller-supplied `owner` parameter against the allowlist instead of the actual caller (`sender`). Because `owner` is an arbitrary address passed by the depositor, any non-allowlisted address can bypass the guard by supplying any allowlisted address as `owner`, rendering the deposit allowlist completely ineffective.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` address (the intended LP-position owner) and passes both `msg.sender` (as `sender`) and `owner` to the `_beforeAddLiquidity` hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both values to every configured extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first unnamed parameter and `owner` as its second. The guard checks `owner`, not `sender`: [3](#0-2) 

The contract's own NatSpec states the extension "Gates `addLiquidity` by depositor address." The depositor is the address that actually calls the function — `msg.sender` of `addLiquidity`, forwarded as `sender`. `owner` is an arbitrary value the caller writes into the call; it is never verified to equal `msg.sender` anywhere in the pool.

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual caller): [4](#0-3) 

---

### Impact Explanation

Any non-allowlisted address can call `addLiquidity(allowlistedAddress, salt, deltas, ...)`. The extension evaluates `allowedDepositor[pool][allowlistedAddress]`, which is `true`, so the guard passes. The attacker's tokens are pulled via the swap callback and credited as LP shares to `allowlistedAddress`. Consequences:

- **Allowlist guard is completely broken**: the entire access-control invariant of `DepositAllowlistExtension` is defeated by any caller.
- **Unrestricted pool-state manipulation**: an attacker can shift bin positions (`curBinIdx`, `curPosInBin`) in a pool that is supposed to be restricted, affecting swap prices and LP value for legitimate LPs.
- **Griefing / forced LP positions**: the attacker can force unwanted, irremovable LP positions onto any allowlisted address (since `removeLiquidity` enforces `msg.sender == owner`). [5](#0-4) 

---

### Likelihood Explanation

Likelihood is high. The bypass requires no special privilege, no flash loan, and no complex setup. Any EOA or contract can call `addLiquidity` with a known allowlisted address as `owner`. Allowlisted addresses are discoverable on-chain via the `AllowedToDepositSet` events emitted by `setAllowedToDeposit`. [6](#0-5) 

---

### Recommendation

Replace the `owner` check with a `sender` check in `beforeAddLiquidity`:

```solidity
// current (wrong):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}

// fixed:
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured in `BEFORE_ADD_LIQUIDITY_ORDER`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Alice is the only allowlisted depositor.
3. Attacker (Bob, not allowlisted) calls:
   ```solidity
   pool.addLiquidity(
       alice,          // owner — allowlisted, passes the guard
       0,              // salt
       deltas,         // bins and shares to add
       callbackData,   // Bob's tokens are pulled here
       ""
   );
   ```
4. `_beforeAddLiquidity(msg.sender=Bob, owner=Alice, ...)` is called.
5. Extension evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
6. Bob's tokens enter the pool; LP shares are minted to Alice.
7. Bob has bypassed the allowlist and altered pool bin state without being a permitted depositor. [7](#0-6) [1](#0-0)

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
