### Title
Allowlist Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass Deposit Restriction — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` ignores the `sender` parameter (the actual pool caller) and instead validates `owner` (the freely-settable LP position recipient). Because `owner` is caller-controlled, any non-allowlisted address can bypass the deposit gate by passing an allowlisted address as `owner`.

### Finding Description

The pool calls the extension with two distinct addresses:

```
_beforeAddLiquidity(msg.sender /*sender=actual caller*/, owner /*LP recipient*/, ...)
``` [1](#0-0) 

The extension receives both but silently discards `sender` (the first, unnamed parameter) and only checks `owner`:

```solidity
function beforeAddLiquidity(address /*sender ignored*/, address owner, ...)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [2](#0-1) 

`owner` is a free parameter in `pool.addLiquidity`: [3](#0-2) 

The `MetricOmmPoolLiquidityAdder` also accepts an arbitrary `owner` and only validates it is non-zero: [4](#0-3) 

**Bypass path:**
1. Attacker (non-allowlisted) calls `pool.addLiquidity(owner=allowlistedAddress, ...)` directly or via `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, allowlistedAddress, ...)`.
2. Pool calls `_beforeAddLiquidity(sender=attacker, owner=allowlistedAddress, ...)`.
3. Extension evaluates `allowedDepositor[pool][allowlistedAddress]` → `true` → passes.
4. Pool mints LP shares to `allowlistedAddress`; callback pulls tokens from the attacker.
5. Deposit succeeds despite the attacker never being on the allowlist.

The `sender` field — which carries the actual caller identity — is available in the hook signature per the interface: [5](#0-4) 

but is never read.

### Impact Explanation

The deposit allowlist is completely defeated. Any non-allowlisted address can add liquidity to a restricted pool by nominating an allowlisted address as `owner`. The pool admin's access control intent (KYC, whitelist-only pools, regulatory gating) is rendered ineffective. This is a broken core pool functionality: the guard that the pool was configured to enforce does not gate the economically relevant actor (the payer/caller).

### Likelihood Explanation

Exploitable by any EOA or contract with no special privileges. The only requirement is knowledge of one allowlisted address for the target pool, which is readable from the public `allowedDepositor` mapping or emitted `AllowedToDepositSet` events. Both the direct pool path and the `MetricOmmPoolLiquidityAdder` path are affected.

### Recommendation

Replace the `owner` check with the `sender` parameter (the actual pool caller):

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

This ensures the check gates the address that actually initiates and pays for the deposit, not the LP position recipient.

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_allowlistBypass_ownerVsSender() public {
    // Setup: pool with DepositAllowlistExtension, only `allowedLP` is allowlisted
    address allowedLP   = makeAddr("allowedLP");
    address attacker    = makeAddr("attacker");
    extension.setAllowedToDeposit(address(pool), allowedLP, true);

    // Attacker is NOT on the allowlist
    assertFalse(extension.allowedDepositor(address(pool), attacker));

    // Attacker calls addLiquidity with owner=allowedLP
    vm.startPrank(attacker);
    token0.approve(address(pool), type(uint256).max);
    token1.approve(address(pool), type(uint256).max);
    // This should revert but does NOT — allowlist checks owner, not sender
    pool.addLiquidity(allowedLP, salt, deltas, callbackData, "");
    vm.stopPrank();

    // LP shares minted to allowedLP despite attacker never being allowlisted
    assertGt(stateView.positionBinShares(address(pool), allowedLP, salt, binIdx), 0);
}
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-40)
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
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L156-162)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external returns (uint256 amount0Added, uint256 amount1Added);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
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
