Audit Report

## Title
Wrong-actor binding in `DepositAllowlistExtension.beforeAddLiquidity` allows unauthorized depositors to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual depositor who pays tokens via callback) and instead checks `owner` (the position recipient) against the allowlist. Because `MetricOmmPool.addLiquidity` imposes no `msg.sender == owner` constraint — unlike `removeLiquidity` which enforces `if (msg.sender != owner) revert NotPositionOwner()` — any unprivileged caller can supply an allowlisted address as `owner`, pass the gate, pay tokens themselves, and mint LP shares to that address, fully bypassing the pool admin's depositor restriction.

## Finding Description

**Root cause — wrong parameter consumed:**

`IMetricOmmExtensions.beforeAddLiquidity` is defined with two distinct address parameters:

```solidity
// metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol L14-20
function beforeAddLiquidity(
    address sender,   // ← actual msg.sender of addLiquidity (the payer)
    address owner,    // ← position recipient
    ...
) external returns (bytes4);
```

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both:

```solidity
// metric-core/contracts/ExtensionCalling.sol L95-98
_callExtensionsInOrder(
    BEFORE_ADD_LIQUIDITY_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
);
```

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`DepositAllowlistExtension.beforeAddLiquidity` silently drops `sender` (unnamed, ignored) and gates only on `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

**Missing sender == owner constraint in addLiquidity:**

`removeLiquidity` enforces `if (msg.sender != owner) revert NotPositionOwner()` at line 206, but `addLiquidity` has no equivalent guard, so `owner` is a free caller-controlled parameter.

**Exploit path:**

1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only `alice`.
2. Attacker (not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
3. Pool calls `_beforeAddLiquidity(msg.sender=attacker, owner=alice, ...)`.
4. Extension checks `allowedDepositor[pool][alice]` → `true`; check passes.
5. `LiquidityLib.addLiquidity` mints LP shares to `alice`; attacker pays tokens via the modify-liquidity callback.
6. Attacker has deposited into a restricted pool without being allowlisted.

**Existing guards reviewed and shown insufficient:**

- `allowAllDepositors` / `allowedDepositor` mappings are keyed correctly by pool address (`msg.sender` in the extension = pool), but the depositor key is `owner` instead of the first (unnamed, ignored) `sender` argument.
- `BaseMetricExtension.onlyPool` only verifies the caller is a registered pool; it does not fix the actor binding.
- `MetricOmmPoolLiquidityAdder._validateOwner` applies only to the periphery adder path; direct pool calls are unrestricted.

## Impact Explanation
The deposit allowlist — a core pool admin access-control mechanism — is fully bypassed by any unprivileged caller. The attacker can force tokens into a restricted pool and mint LP shares to any allowlisted address without that address's consent, violating the pool admin's depositor restriction. This constitutes a broken core pool functionality / admin-boundary break: an unprivileged path circumvents a configured access control that the pool admin explicitly set.

## Likelihood Explanation
Exploitation requires only a standard EOA or contract that implements the modify-liquidity callback. No privileged role, flash loan, or special token behavior is needed. The attacker must know one allowlisted address (publicly readable from `allowedDepositor`) and have enough tokens to pay the callback. The attack is repeatable at will on any pool using this extension.

## Recommendation
Replace the ignored first parameter with a named `sender` variable and gate on it instead of `owner`:

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

This aligns with the extension's NatSpec ("Gates `addLiquidity` by depositor address") and with the analogous `SwapAllowlistExtension`, which correctly gates on `sender`.

## Proof of Concept

```solidity
// Foundry test sketch
function test_depositAllowlistBypass() public {
    // Pool admin allowlists only alice, not attacker
    extension.setAllowedToDeposit(address(pool), alice, true);

    // Attacker (not allowlisted) calls addLiquidity with owner = alice
    vm.startPrank(attacker);
    token0.approve(address(pool), type(uint256).max);
    token1.approve(address(pool), type(uint256).max);

    // Should revert with NotAllowedToDeposit — but does NOT
    pool.addLiquidity(
        alice,      // owner = allowlisted address
        0,          // salt
        deltas,
        callbackData,
        ""
    );
    vm.stopPrank();

    // Alice now holds LP shares she never requested; attacker bypassed the allowlist
    assertGt(positionShares(alice, 0, binIdx), 0);
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-206)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-20)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);
```
