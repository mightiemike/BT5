Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and instead checks `owner` (a freely chosen argument). Because `addLiquidity` imposes no constraint between `msg.sender` and `owner`, any unprivileged address can bypass the allowlist by supplying an already-allowlisted address as `owner`, completely neutralizing the extension's access control.

## Finding Description
The admin-facing API names its mapping and setter using `depositor`, signaling intent to gate by the actual token provider: [1](#0-0) [2](#0-1) 

However, the hook implementation ignores the first parameter (`sender`) and checks the second (`owner`): [3](#0-2) 

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` argument as `owner` to `_beforeAddLiquidity`: [4](#0-3) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both values to the extension: [5](#0-4) 

Unlike `removeLiquidity`, which enforces `msg.sender == owner` before calling the hook, `addLiquidity` has no such constraint: [6](#0-5) 

Because `owner` is freely chosen by the caller, any address can pass the allowlist check by naming an allowlisted address as `owner`. The extension evaluates `allowedDepositor[pool][owner]` — which is `true` for the named allowlisted address — and does not revert, regardless of who `sender` is.

## Impact Explanation
The deposit allowlist is completely neutralized. An unauthorized address can deposit tokens into a restricted pool by setting `owner` to any allowlisted address. The unauthorized caller provides the tokens via the `addLiquidity` callback path while the allowlisted address receives the LP shares. This breaks the pool admin's sole mechanism for restricting liquidity provision to trusted counterparties. Pools relying on this guard for institutional or compliance-gated liquidity have no effective access control on deposits. This constitutes a broken core pool functionality causing loss of the admin-boundary guarantee and enabling unauthorized fund flows into restricted pools.

## Likelihood Explanation
Exploitation requires no special privilege. Any address that can call `addLiquidity` on the pool can exploit this in a single transaction. The only prerequisite is knowing one allowlisted address, which is discoverable from on-chain `AllowedToDepositSet` events emitted by `setAllowedToDeposit`.

## Recommendation
Replace the `owner` check with `sender` in `beforeAddLiquidity`:

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

This aligns the runtime check with the documented intent ("gate by depositor address") and with the naming of the admin API (`depositor`, `allowedDepositor`).

## Proof of Concept
1. Pool is deployed with `DepositAllowlistExtension` as a `beforeAddLiquidity` hook; `allowAllDepositors[pool] = false`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is not allowlisted.
3. Bob calls `pool.addLiquidity(owner = alice, salt, deltas, callbackData, extensionData)`.
4. Pool calls `_beforeAddLiquidity(sender = bob, owner = alice, ...)`.
5. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` executes; Bob's tokens are pulled via callback; Alice's position is credited.
7. Bob has deposited into a pool he is not authorized to access. The allowlist is fully bypassed.

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-14)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-19)
```text
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

**File:** metric-core/contracts/MetricOmmPool.sol (L204-207)
```text
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
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
