### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Unprivileged Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
`DepositAllowlistExtension` is documented as gating `addLiquidity` by **depositor address**. Its `beforeAddLiquidity` hook silently drops the `sender` argument and checks `owner` instead. Because `MetricOmmPool.addLiquidity` imposes no constraint that `msg.sender == owner`, any caller not on the allowlist can bypass the guard by naming an allowlisted address as `owner`.

### Finding Description
`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

- `sender` = `msg.sender` — the actual caller who will supply tokens via the swap-callback
- `owner` = the first function argument — the address that will receive the LP-position shares [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both: [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is unnamed and therefore silently ignored. The allowlist lookup is performed against `owner`: [3](#0-2) 

The contract's own naming (`allowedDepositor`, `setAllowedToDeposit`, `AllowedToDepositSet`) and its NatSpec ("Gates `addLiquidity` by depositor address") all confirm the intended subject is the depositor (`sender`), not the position recipient (`owner`): [4](#0-3) 

`addLiquidity` has no `require(msg.sender == owner)` guard (unlike `removeLiquidity`, which does enforce this): [5](#0-4) [6](#0-5) 

Therefore any caller can pass an allowlisted address as `owner`, satisfy the check, and deposit freely.

### Impact Explanation
The deposit allowlist guard is rendered completely ineffective. Any unprivileged actor can add liquidity to a pool whose admin intended to restrict deposits to a curated set of addresses. Unauthorized deposits alter bin balances, `curPosInBin`, and `binTotals`, directly affecting the oracle-derived swap prices that subsequent traders receive. In a pool where the allowlist exists to enforce risk controls or regulatory constraints, this constitutes broken core pool functionality with potential for LP-value dilution and swap-price manipulation.

### Likelihood Explanation
Exploitation requires only a standard `addLiquidity` call with an allowlisted address supplied as `owner`. No special role, flash loan, or privileged access is needed. The attacker supplies their own tokens (via callback) and the LP shares are minted to the named `owner`; the attacker's cost is the deposited capital, which they can recover by having the `owner` later call `removeLiquidity`. Likelihood is **High**.

### Recommendation
Bind the allowlist check to `sender` (the actual depositor) rather than `owner`:

```solidity
function beforeAddLiquidity(
    address sender,   // ← name and use this parameter
    address,          // owner — not the depositor
    uint80,
    LiquidityDelta calldata,
    bytes calldata
) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` attached to `beforeAddLiquidity`.
2. Admin calls `setAllowedToDeposit(pool, alice, true)` — only Alice is meant to deposit.
3. Bob (not allowlisted) constructs a call:
   ```solidity
   pool.addLiquidity(
       alice,          // owner  ← allowlisted; extension checks this
       salt,
       deltas,
       callbackData,   // Bob's callback supplies Bob's tokens
       extensionData
   );
   ```
4. `_beforeAddLiquidity(sender=Bob, owner=Alice, ...)` is dispatched.
5. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` mints shares to Alice; Bob's tokens enter the pool.
7. Bob has deposited into a pool he was explicitly barred from, bypassing the guard entirely. [3](#0-2)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-19)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
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
