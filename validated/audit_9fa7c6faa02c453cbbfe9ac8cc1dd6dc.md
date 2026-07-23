Audit Report

## Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and gates on `owner` (the caller-supplied LP position recipient). Because `owner` is a free parameter with no pool-level constraint, any address not on the allowlist can bypass the restriction by calling `addLiquidity` with `owner` set to any allowlisted address, depositing tokens into that address's LP position without authorization.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to `_beforeAddLiquidity`: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes both and forwards them to the extension hook: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` declares the first parameter (`sender`) as unnamed, discarding it entirely, and evaluates `allowedDepositor[msg.sender][owner]` — where `msg.sender` is the pool and `owner` is the attacker-controlled recipient: [3](#0-2) 

The pool's `addLiquidity` imposes no restriction on who `owner` can be — unlike `removeLiquidity`, which enforces `msg.sender == owner`: [4](#0-3) 

The contract's own NatSpec documents the intent as gating by depositor address: [5](#0-4) 

The exploit path: attacker discovers an allowlisted address on-chain via `AllowedToDepositSet` events, then calls `pool.addLiquidity(owner=allowlisted_address, ...)`. The extension checks `allowedDepositor[pool][allowlisted_address] == true` and passes. The attacker's tokens are deposited and credited to the allowlisted address's LP position. No privileged access is required.

## Impact Explanation
The `DepositAllowlistExtension` allowlist is completely bypassed. Any address — regardless of allowlist status — can add liquidity to a restricted pool by naming any allowlisted address as `owner`. The pool admin's access control (e.g., KYC/compliance gating) is rendered entirely ineffective. Additionally, the unauthorized caller's tokens are deposited and credited to the allowlisted address's LP position without that address's consent, constituting unsolicited griefing of the position owner's accounting. This is an admin-boundary break: an unprivileged path bypasses a pool admin-configured access control mechanism.

## Likelihood Explanation
The attack requires only a public `addLiquidity` call with a known allowlisted address as `owner`. No privileged access, special tokens, flash loans, or oracle manipulation is needed. Allowlisted addresses are discoverable on-chain via `AllowedToDepositSet` events. The attack is repeatable at any time by any address.

## Recommendation
Replace the `owner` check with the `sender` argument (the actual caller):

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

## Proof of Concept

```solidity
// attacker is NOT on the allowlist; allowlisted is on the allowlist
address attacker = makeAddr("attacker");
address allowlisted = makeAddr("allowlisted");

// pool admin allowlists only `allowlisted`
vm.prank(admin);
depositExtension.setAllowedToDeposit(address(pool), allowlisted, true);

// attacker calls addLiquidity with owner = allowlisted
// extension checks allowedDepositor[pool][allowlisted] == true → passes
vm.prank(attacker);
pool.addLiquidity(
    allowlisted,   // owner = allowlisted address
    0,
    deltas,
    callbackData,
    ""
);

// attacker successfully deposited despite not being on the allowlist
uint256 shares = pool.positionBinShares(allowlisted, 0, binIdx);
assertGt(shares, 0); // passes — allowlist bypassed
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-11)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
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
