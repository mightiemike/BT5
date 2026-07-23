The vulnerability is confirmed. The call chain is unambiguous:

**Trace:**

1. Attacker calls `pool.addLiquidity(owner=allowlistedAddr, ...)` directly. [1](#0-0) 

2. Pool passes `msg.sender` (attacker) as `sender` and the caller-supplied `owner` (allowlisted address) to `_beforeAddLiquidity`. [2](#0-1) 

3. `DepositAllowlistExtension.beforeAddLiquidity` receives both, but the first parameter (`sender` = attacker) is **unnamed and silently discarded**. The check uses only `owner` (the allowlisted address the attacker supplied) against `msg.sender` (the pool address). [3](#0-2) 

`allowedDepositor[pool][allowlistedAddr]` is `true` → check passes → attacker's deposit proceeds.

---

### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
`DepositAllowlistExtension.beforeAddLiquidity` ignores the `sender` argument (the actual `msg.sender` of the pool call) and instead validates `owner`, which is a free parameter the caller supplies to `pool.addLiquidity`. Any unprivileged address can bypass the allowlist by passing an allowlisted address as `owner`.

### Finding Description
`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` and passes both `msg.sender` (as `sender`) and `owner` to the extension hook:

```solidity
// MetricOmmPool.sol:191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

The extension receives `(address sender, address owner, ...)` but discards `sender` entirely (unnamed parameter) and gates on `owner`:

```solidity
// DepositAllowlistExtension.sol:32-38
function beforeAddLiquidity(address, address owner, ...)
  external view override returns (bytes4)
{
  if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert ...NotAllowedToDeposit();
  }
```

Because `owner` is attacker-controlled, any caller can pass `owner = allowlistedAddr` and the check resolves to `allowedDepositor[pool][allowlistedAddr] == true`, which passes. The attacker then satisfies the token callback themselves, and the position is recorded under `allowlistedAddr`.

### Impact Explanation
The deposit allowlist — the pool's primary curation mechanism — is completely ineffective. Any unprivileged address can deposit into a restricted pool, bypassing the admin's access control. This constitutes a broken core pool functionality: the pool admin cannot enforce who provides liquidity, and pool state (bin liquidity, pricing) can be manipulated by unauthorized actors. Shares are credited to the allowlisted address, which also receives an unexpected position they did not create.

### Likelihood Explanation
Exploiting this requires only a direct call to `pool.addLiquidity` with `owner` set to any known allowlisted address and a callback implementation that pays tokens. No privileged access, no special setup, and no non-standard token behavior is needed. Any pool deploying `DepositAllowlistExtension` is affected.

### Recommendation
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

### Proof of Concept
```solidity
// Foundry integration test sketch
contract AttackerCallback is IMetricOmmModifyLiquidityCallback {
    IERC20 t0; IERC20 t1;
    constructor(address _t0, address _t1) { t0 = IERC20(_t0); t1 = IERC20(_t1); }
    function metricOmmModifyLiquidityCallback(uint256 a0, uint256 a1, bytes calldata) external {
        if (a0 > 0) t0.transfer(msg.sender, a0);
        if (a1 > 0) t1.transfer(msg.sender, a1);
    }
}

function test_bypassDepositAllowlist() public {
    // allowlistedAddr is the only address allowed to deposit
    address allowlistedAddr = makeAddr("allowlisted");
    depositExtension.setAllowedToDeposit(address(pool), allowlistedAddr, true);

    // attacker is NOT on the allowlist
    address attacker = makeAddr("attacker");
    AttackerCallback cb = new AttackerCallback(address(token0), address(token1));
    deal(address(token0), address(cb), 1e18);
    deal(address(token1), address(cb), 1e18);

    // attacker calls pool directly with owner=allowlistedAddr
    vm.prank(attacker);
    pool.addLiquidity(allowlistedAddr, 0, deltas, "", "");

    // assert: deposit succeeded, position credited to allowlistedAddr
    // allowlist was bypassed — attacker (not allowlisted) caused the deposit
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
