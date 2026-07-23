Audit Report

## Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of the pool call) and gates on `owner`, which is a free parameter the caller supplies to `pool.addLiquidity`. Any unprivileged address can bypass the allowlist by passing an allowlisted address as `owner`, completely defeating the pool's primary access-control mechanism.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both values faithfully: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `(address sender, address owner, ...)` but the first parameter is unnamed and silently discarded. The guard checks only `owner`: [3](#0-2) 

Because `owner` is attacker-controlled, any caller can pass `owner = allowlistedAddr` and the check resolves to `allowedDepositor[pool][allowlistedAddr] == true`, which passes. The attacker satisfies the token callback themselves, and the position is recorded under `allowlistedAddr`. The `sender` (actual depositing actor) is never validated.

## Impact Explanation
The deposit allowlist — the pool's primary curation mechanism — is completely ineffective. Any unprivileged address can deposit into a restricted pool, bypassing the admin's access control. This constitutes broken core pool functionality: the pool admin cannot enforce who provides liquidity, pool state (bin liquidity, pricing) can be manipulated by unauthorized actors, and shares are credited to the allowlisted address which receives an unexpected position it did not create.

## Likelihood Explanation
Exploiting this requires only a direct call to `pool.addLiquidity` with `owner` set to any known allowlisted address and a callback implementation that pays the required tokens. No privileged access, no special setup, and no non-standard token behavior is needed. Any pool deploying `DepositAllowlistExtension` is affected immediately upon deployment.

## Recommendation
Change `beforeAddLiquidity` to check `sender` (the actual depositing actor) rather than `owner`:

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
contract AttackerCallback is IMetricOmmModifyLiquidityCallback {
    IERC20 t0; IERC20 t1;
    constructor(address _t0, address _t1) { t0 = IERC20(_t0); t1 = IERC20(_t1); }
    function metricOmmModifyLiquidityCallback(uint256 a0, uint256 a1, bytes calldata) external {
        if (a0 > 0) t0.transfer(msg.sender, a0);
        if (a1 > 0) t1.transfer(msg.sender, a1);
    }
}

function test_bypassDepositAllowlist() public {
    address allowlistedAddr = makeAddr("allowlisted");
    depositExtension.setAllowedToDeposit(address(pool), allowlistedAddr, true);

    address attacker = makeAddr("attacker");
    AttackerCallback cb = new AttackerCallback(address(token0), address(token1));
    deal(address(token0), address(cb), 1e18);
    deal(address(token1), address(cb), 1e18);

    // attacker (not allowlisted) calls pool with owner=allowlistedAddr
    vm.prank(attacker);
    pool.addLiquidity(allowlistedAddr, 0, deltas, "", "");
    // deposit succeeds — allowlist bypassed
}
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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
