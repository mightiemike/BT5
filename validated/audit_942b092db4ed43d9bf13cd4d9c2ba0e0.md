Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any unapproved operator to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension` is designed to gate `addLiquidity` calls to approved depositors. Its `beforeAddLiquidity` hook receives `sender` (the actual `msg.sender` of `addLiquidity`) as its first argument but silently discards it (unnamed parameter) and checks only `owner` (the position recipient). Because `MetricOmmPool.addLiquidity` imposes no `msg.sender == owner` requirement, any unapproved operator can call `addLiquidity(approvedOwner, ...)` and pass the gate entirely.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as separate arguments to the hook dispatcher: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes both into the extension call: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first `address` argument but leaves it unnamed (discarded), then checks only `owner`: [3](#0-2) 

The guard `allowedDepositor[msg.sender][owner]` (where `msg.sender` is the pool) checks whether the **position recipient** is approved, not whether the **actual caller** is approved. `addLiquidity` has no `msg.sender == owner` enforcement: [4](#0-3) 

Contrast with `removeLiquidity`, which does enforce `msg.sender == owner`: [5](#0-4) 

Exploit path: any unapproved EOA or contract calls `pool.addLiquidity(approvedOwner, salt, deltas, callbackData, "")`. The hook fires, checks `allowedDepositor[pool][approvedOwner]` which is `true`, and returns the success selector. The unapproved caller pays tokens via the callback and receives shares credited to `approvedOwner`. The allowlist is fully bypassed.

## Impact Explanation
The deposit allowlist is the sole access-control mechanism of `DepositAllowlistExtension`. Its bypass means a pool configured to accept deposits only from KYC'd or whitelisted addresses will accept deposits from any arbitrary caller, as long as they name an approved address as `owner`. This breaks the core functionality the extension exists to provide — broken core pool functionality causing the admin's restriction to be rendered completely ineffective.

## Likelihood Explanation
The bypass requires only a direct call to `pool.addLiquidity(approvedOwner, ...)` from any EOA or contract. No privileged access, no special setup, and no malicious token behavior is needed. Approved owner addresses are publicly readable from `allowedDepositor`. Likelihood is high.

## Recommendation
Replace the `owner` check with the `sender` argument (the first, currently unnamed parameter):

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
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

If the intent is to gate on the position owner (e.g., KYC on position holders), the current `owner` check is correct but the extension must also enforce `sender == owner`, or document that operator-on-behalf-of-owner is an accepted pattern.

## Proof of Concept

```solidity
// Foundry test sketch
function test_operatorBypassesAllowlist() public {
    // owner is allowlisted, operator is not
    depositExt.setAllowedToDeposit(address(pool), owner, true);

    // operator calls addLiquidity naming owner as position recipient
    vm.prank(operator); // operator NOT in allowedDepositor
    pool.addLiquidity(owner, 0, deltas, callbackData, "");
    // succeeds — allowlist gate is bypassed
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

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-39)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
```
