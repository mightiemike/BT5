### Title
`DepositAllowlistExtension` checks position `owner` instead of transaction `sender`, allowing any caller to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual caller of `addLiquidity`) and instead gates on the `owner` argument (the position recipient). Because `MetricOmmPool.addLiquidity` imposes no restriction on who may supply an arbitrary `owner`, any unprivileged address can bypass the allowlist by naming an already-allowlisted address as the position owner.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` and forwards both `msg.sender` (as `sender`) and `owner` to the `_beforeAddLiquidity` hook:

```solidity
// MetricOmmPool.sol:191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes both and passes them to every configured extension:

```solidity
// ExtensionCalling.sol:97
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` then drops the first argument entirely and checks only `owner`:

```solidity
// DepositAllowlistExtension.sol:32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

The contract's own NatSpec states it "Gates `addLiquidity` by **depositor** address," but the depositor (`sender`) is never read. The parallel `SwapAllowlistExtension` correctly checks `sender`:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [4](#0-3) 

Because `removeLiquidity` enforces `msg.sender == owner`, the attacker cannot reclaim the deposited tokens — but the bypass itself is unconditional and requires no special privilege. [5](#0-4) 

---

### Impact Explanation

The deposit allowlist is the primary admin-configured access-control boundary for liquidity entry. Its complete bypass by any unprivileged caller constitutes an admin-boundary break under the allowed impact gate. Concrete consequences:

1. **Unauthorized liquidity injection**: An attacker adds liquidity at chosen bins, altering per-bin token balances and `curPosInBin` state. This directly affects the marginal price seen by subsequent swappers and the per-share value metrics consumed by `OracleValueStopLossExtension`, potentially suppressing or triggering the stop-loss guard in ways the pool admin did not authorize.
2. **Allowlisted-address griefing**: The position is credited to the named allowlisted address. That address receives an unsolicited position it did not create, complicating its own accounting and potentially locking it into a bin it would not have chosen.
3. **Invariant break**: The pool admin's explicit intent — that only vetted depositors may supply liquidity — is violated without any privileged action.

---

### Likelihood Explanation

- The `owner` parameter of `addLiquidity` is fully caller-controlled with no on-chain restriction.
- Any allowlisted address is publicly discoverable (emitted in `AllowedToDepositSet` events or readable from `allowedDepositor`).
- No special token, role, or setup is required beyond knowing one allowlisted address and holding enough tokens to fund the callback.
- The attacker does not need to profit directly; the bypass is useful for pool-state manipulation or griefing.

---

### Recommendation

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

If the intended semantic is to allowlist position owners rather than callers, the NatSpec and the mapping key name (`allowedDepositor`) must be updated to reflect that, and a separate caller-level guard should be added.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with DepositAllowlistExtension.
2. Pool admin calls setAllowedToDeposit(pool, alice, true).
   alice is the only allowlisted depositor.

Attack
──────
3. bob (not allowlisted) calls:
       pool.addLiquidity(
           owner        = alice,   // allowlisted address
           salt         = 0,
           deltas       = <chosen bins>,
           callbackData = "",
           extensionData= ""
       )

4. DepositAllowlistExtension.beforeAddLiquidity fires:
       allowedDepositor[pool][alice] == true  →  check passes

5. LiquidityLib credits the position to alice.
   bob's metricOmmSwapCallback is invoked; bob transfers tokens to the pool.

6. bob has added liquidity to a restricted pool without being allowlisted.
   alice holds an unsolicited position she did not create.
   The pool's bin state is now altered by bob's chosen deltas.
```

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L95-99)
```text
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
