Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks position `owner` instead of actual depositor `sender`, allowing allowlist bypass via `MetricOmmPoolLiquidityAdder` — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument and gates only on `owner` (the LP position recipient). Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` lets any caller freely specify an arbitrary `owner` with no restriction beyond non-zero, a non-allowlisted user can bypass the deposit allowlist by naming any allowlisted address as `owner`, paying the tokens themselves, and causing LP shares to be minted to that address. The pool admin's configured access-control boundary is fully defeated.

## Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` is declared with the first parameter (`sender`) explicitly unnamed and discarded:

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

The pool correctly forwards both `sender` (the `msg.sender` of `addLiquidity`) and `owner` to the extension via `_beforeAddLiquidity`:

```solidity
// metric-core/contracts/ExtensionCalling.sol L88-99
function _beforeAddLiquidity(address sender, address owner, ...) internal {
    _callExtensionsInOrder(
        BEFORE_ADD_LIQUIDITY_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
}
```

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts a caller-controlled `owner` and validates only that it is non-zero:

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol L56-68
function addLiquidityExactShares(address pool, address owner, ...) external payable override {
    _validateOwner(owner);   // only checks owner != address(0)
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
}
```

`_addLiquidity` then calls `pool.addLiquidity(positionOwner, ...)` where `positionOwner` is the attacker-supplied `owner`. The pool receives `msg.sender = liquidityAdder` as `sender` and the attacker-supplied address as `owner`. The extension ignores `sender` entirely and checks only `allowedDepositor[pool][owner]`. If the attacker supplies any allowlisted address as `owner`, the check passes unconditionally, while the actual payer (the attacker, stored in transient context at `T_SLOT_PAY_PAYER`) is never checked.

## Impact Explanation

The deposit allowlist — the pool admin's primary access-control mechanism for curated pools — is completely bypassed. Any non-allowlisted user can trigger LP share minting into a restricted pool by naming an allowlisted address as `owner`. The attacker's tokens are pulled and LP shares are minted to the allowlisted address. Beyond the direct allowlist bypass, this allows an unprivileged caller to arbitrarily alter pool state (bin liquidity distribution, cursor position) in a pool that was designed to restrict who may do so. The invariant "only allowlisted depositors may mint LP shares" is broken for any pool using this extension.

## Likelihood Explanation

The attack path is fully permissionless. Any user can call `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` with an arbitrary `owner`. No special role, no admin cooperation, and no non-standard token behavior is required. The only prerequisite is knowing one allowlisted address, which is publicly readable on-chain via the `allowedDepositor` mapping or `AllowedToDepositSet` events emitted by `setAllowedToDeposit`.

## Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual `msg.sender` of `pool.addLiquidity`, i.e., the router or direct caller) instead of `owner`:

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

For router-mediated flows where `sender` is the router contract rather than the end user, consider also propagating the true payer through `extensionData` or a dedicated callback, and verifying it in the extension. Update `isAllowedToDeposit`, the `allowedDepositor` mapping, and `setAllowedToDeposit` to reflect that the checked address is the depositor/sender, not the position owner.

## Proof of Concept

```solidity
// Setup: pool configured with DepositAllowlistExtension.
//        allowedUser is allowlisted: allowedDepositor[pool][allowedUser] == true.
//        attacker is NOT allowlisted.

// Step 1: attacker calls the liquidity adder with allowedUser as owner.
liquidityAdder.addLiquidityExactShares(
    pool,
    allowedUser,   // allowlisted address controlled by attacker as parameter
    salt,
    deltas,
    maxAmount0,
    maxAmount1,
    ""
);
// Pool calls _beforeAddLiquidity(liquidityAdder, allowedUser, ...).
// Extension checks allowedDepositor[pool][allowedUser] == true → passes.
// Attacker's tokens are pulled via transient payer context (msg.sender of outer call).
// LP shares minted to allowedUser.

// Step 2 (optional): allowedUser removes liquidity and returns tokens to attacker off-chain.
// Net result: attacker deposited into a curated pool with zero allowlist enforcement.
// Pool state (bin liquidity, cursor) is modified by an unprivileged actor.
```