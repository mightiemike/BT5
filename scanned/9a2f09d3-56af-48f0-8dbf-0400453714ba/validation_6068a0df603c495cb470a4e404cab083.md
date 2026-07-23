### Title
`DepositAllowlistExtension.beforeAddLiquidity` Gates the Wrong Identity (`owner` Instead of `sender`), Allowing Any Unpermissioned Depositor to Bypass the Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension` is described as "Gates `addLiquidity` by depositor address, per pool." The actual token depositor is `sender` (`msg.sender` of the `addLiquidity` call, who pays via the modify-liquidity callback). However, `beforeAddLiquidity` silently discards `sender` and checks `owner` (the position recipient) instead. Any unpermissioned address can bypass the allowlist by calling `addLiquidity(allowlisted_owner, ...)`, paying the tokens themselves while the allowlisted owner receives the position.

### Finding Description

`MetricOmmPool.addLiquidity` explicitly supports an operator pattern: `msg.sender` (the `sender`) pays tokens via callback, while `owner` (a separate address) receives the LP position. The pool passes both to the extension hook:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but discards it (unnamed `address`), then checks only `owner`:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The allowlist admin registers addresses they intend to permit as depositors. Because the check is on `owner` rather than `sender`, an attacker who is **not** on the allowlist can pass the guard by supplying any allowlisted address as `owner`. The attacker's own address (`sender`) is never evaluated.

This is the direct structural analog to the `GovernanceMothership.notifyFor` bug: a function receives a target identity (`account` / `owner`) but evaluates the wrong identity (`msg.sender` / `sender`) — here the inversion is that the wrong field is checked rather than the wrong field used for a balance lookup, but the root cause is identical: the guard evaluates an identity that is not the acting party.

### Impact Explanation

The deposit allowlist is a pool-level access control mechanism. Pools that deploy it intend to restrict which addresses may inject liquidity (e.g., for compliance, whitelisted market-maker programs, or controlled bootstrapping). Because `sender` is never checked, any address — regardless of allowlist status — can deposit tokens into a restricted pool by nominating any allowlisted address as `owner`. The allowlisted owner receives an unsolicited LP position; the pool receives tokens from an unpermissioned source. The pool admin's access control is completely nullified.

### Likelihood Explanation

The `addLiquidity` operator pattern (`sender != owner`) is explicitly documented and supported. Any actor who knows an allowlisted address (which may be publicly observable on-chain from prior deposits) can exploit this with a single transaction. No privileged access, no special token, and no malicious setup is required.

### Recommendation

Replace the unnamed first parameter with `sender` and gate on it, consistent with how `SwapAllowlistExtension` gates `sender` for swaps:

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

If the intent is instead to gate by position owner (who receives the LP shares), the extension description and NatDoc must be corrected and the operator pattern must be documented as a known bypass vector.

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` configured.
2. Admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is permitted.
3. Attacker (`bob`, not on allowlist) calls:
   ```solidity
   pool.addLiquidity(alice, salt, deltas, callbackData, extensionData);
   ```
4. Pool calls `_beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)`.
5. Extension checks `allowedDepositor[pool][alice]` → `true` → no revert.
6. `bob` pays tokens via callback; `alice` receives the LP position.
7. `bob` has successfully deposited into a pool that was supposed to block him.

The `SwapAllowlistExtension` correctly checks `sender` (the actual swapper) at line 37, confirming the asymmetry is a defect in `DepositAllowlistExtension` rather than an intentional design difference. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
