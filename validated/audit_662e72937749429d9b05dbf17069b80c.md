Now I have all the information needed. Let me analyze the exact code path.

The vulnerability is real and provable from the code. Here is the finding:

---

### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
`DepositAllowlistExtension.beforeAddLiquidity` is supposed to gate liquidity additions to curated pools by checking whether the depositing actor is allowlisted. However, it silently discards the `sender` argument (the actual `msg.sender` who called `addLiquidity`) and instead checks the `owner` parameter — which is a free, caller-controlled input with no validation in the pool. Any attacker can pass an allowlisted address as `owner` and the guard passes unconditionally.

### Finding Description

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address from the caller and forwards both `msg.sender` and `owner` to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

There is no requirement that `msg.sender == owner` anywhere in `addLiquidity`.

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but leaves it unnamed and never reads it. The allowlist check is performed against `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [2](#0-1) 

Because `owner` is attacker-controlled and the pool imposes no constraint on it, an attacker who is **not** in `allowedDepositor[pool]` can pass any allowlisted address as `owner`. The guard reads `allowedDepositor[pool][victim] == true` and allows the call through.

### Impact Explanation
Pool curation is broken. A pool admin who deploys `DepositAllowlistExtension` to restrict liquidity provision to a curated set of addresses cannot enforce that restriction. Any unprivileged address can add liquidity to the pool by supplying an allowlisted address as `owner`. The LP position is credited to the `owner` address (not the attacker), but the attacker controls which bins receive liquidity and at what share amounts, allowing manipulation of pool liquidity distribution in a curated pool without authorization. This is a broken core pool functionality finding.

### Likelihood Explanation
The bypass requires only a single public call to `pool.addLiquidity` with a known allowlisted address as `owner`. No privileged access, no special token behavior, and no off-chain data are needed. Any allowlisted address is observable on-chain via `allowedDepositor` or emitted `AllowedToDepositSet` events. [3](#0-2) 

### Recommendation
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

### Proof of Concept

```solidity
// Foundry test sketch
function test_bypassAllowlist() public {
    address victim  = makeAddr("victim");
    address attacker = makeAddr("attacker");

    // Only victim is allowlisted
    vm.prank(admin);
    extension.setAllowedToDeposit(address(pool), victim, true);

    // Attacker is NOT allowlisted
    assertFalse(extension.isAllowedToDeposit(address(pool), attacker));

    // Attacker calls addLiquidity with owner = victim
    // Extension checks allowedDepositor[pool][victim] == true → passes
    vm.prank(attacker);
    pool.addLiquidity(
        victim,          // owner = allowlisted victim
        0,               // salt
        deltas,
        callbackData,
        ""
    );
    // Assert: liquidity was added despite attacker not being allowlisted
}
```

The test will pass (no revert) because `allowedDepositor[pool][victim]` is `true` and the extension never inspects `sender`.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L188-195)
```text
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-20)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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
