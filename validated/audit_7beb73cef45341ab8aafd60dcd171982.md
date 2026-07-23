Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any non-allowlisted caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension` is designed to gate `addLiquidity` to a per-pool allowlist of depositors. Its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual `msg.sender` of the pool call) and validates only `owner` (the caller-supplied position recipient). Because `addLiquidity` imposes no `msg.sender == owner` constraint, any non-allowlisted address can pass the check by supplying an allowlisted address as `owner`, fully bypassing the access-control mechanism.

## Finding Description
`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address with no requirement that `msg.sender == owner`. [1](#0-0) 

It passes `msg.sender` as `sender` and the caller-supplied value as `owner` to `_beforeAddLiquidity`: [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` encodes both and forwards them to the extension: [3](#0-2) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `(sender, owner, ...)` but the first parameter is unnamed (dropped), and the check uses only `owner`: [4](#0-3) 

An attacker who knows any allowlisted address `A` calls `pool.addLiquidity(owner=A, ...)` from a non-allowlisted address `B`. The extension evaluates `allowedDepositor[pool][A] == true` and permits the call. LP shares are minted to `A`, and `B` pays the tokens via the modify-liquidity callback. The `removeLiquidity` path enforces `msg.sender == owner`, so `B` cannot recover the deposited tokens: [5](#0-4) 

The wrong value: `allowedDepositor[msg.sender][owner]` is evaluated instead of `allowedDepositor[msg.sender][sender]`, meaning the extension decision is based on the wrong actor.

## Impact Explanation
The deposit allowlist — the sole access-control mechanism for restricted pools — is fully bypassed by any caller who knows a single allowlisted address. Non-allowlisted actors can add liquidity to restricted pools, and allowlisted users can be forced into LP positions they did not initiate. The attacker permanently loses the deposited tokens (no recovery path via `removeLiquidity`), constituting a direct loss of user principal. This breaks the core invariant of the extension and constitutes broken core pool functionality for any pool relying on `DepositAllowlistExtension` for access control.

## Likelihood Explanation
The attack requires only knowledge of one allowlisted address (publicly readable from the `allowedDepositor` mapping or on-chain events) and the ability to call `pool.addLiquidity` directly (a public, permissionless entrypoint). No privileged access, oracle manipulation, or special token behavior is needed. The preconditions are trivially met for any deployed restricted pool.

## Recommendation
Check `sender` (the actual caller) instead of `owner` in `beforeAddLiquidity`:

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

If the intent is to allow third-party deposits on behalf of an allowlisted `owner` (as the periphery's `addLiquidityExactShares(pool, owner, ...)` supports), then both `sender` and `owner` should be checked: allow the call if either is allowlisted.

## Proof of Concept
```solidity
// Foundry integration test
function test_allowlistBypass() public {
    address allowlisted = makeAddr("allowlisted");
    address attacker    = makeAddr("attacker");

    // Admin allowlists only `allowlisted`
    vm.prank(admin);
    depositExtension.setAllowedToDeposit(address(pool), allowlisted, true);

    // Fund and approve attacker
    token0.mint(attacker, 1_000_000);
    token1.mint(attacker, 1_000_000);
    vm.startPrank(attacker);
    token0.approve(address(pool), type(uint256).max);
    token1.approve(address(pool), type(uint256).max);

    // Attacker calls addLiquidity with owner = allowlisted
    // Extension checks allowedDepositor[pool][allowlisted] == true → passes
    // Call succeeds; shares minted to `allowlisted`, tokens pulled from `attacker`
    pool.addLiquidity(allowlisted, 0, delta, callbackData, "");
    // Assert: call did NOT revert with NotAllowedToDeposit
    vm.stopPrank();
}
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

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
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
