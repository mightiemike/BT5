### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing allowlist bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
`DepositAllowlistExtension` is documented as gating `addLiquidity` by **depositor** address. Its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`, i.e., the address that provides tokens via callback) and instead checks `owner` (the LP-position beneficiary). Any unprivileged address can bypass the allowlist by supplying an allowlisted address as `owner`, or conversely, an allowlisted router is blocked from depositing on behalf of any non-allowlisted `owner`.

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both arguments faithfully:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first positional argument (`sender`) is unnamed and unused. The guard is evaluated against `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual caller of `swap`):

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [4](#0-3) 

The asymmetry is the root cause. The NatDoc and the parameter name `depositor` in `setAllowedToDeposit` confirm the intent is to gate the actual depositing address, not the LP-position owner. [5](#0-4) 

### Impact Explanation

**Bypass path (unauthorized deposit):** A non-allowlisted address `B` calls `pool.addLiquidity(allowlistedAddress_A, salt, deltas, callbackData, extensionData)`. The hook receives `sender=B, owner=A`; it checks only `A`, which is allowlisted, so the call succeeds. `B` provides tokens via the swap-callback mechanism and the LP position is minted to `A`. The pool admin's KYC/access-control boundary is silently circumvented by any unprivileged caller.

**Blocking path (broken router integration):** A pool admin allowlists a router `R` as the intended depositor. When a user `U` calls the router, the router calls `pool.addLiquidity(U, ...)`. The hook checks `owner=U`, which is not allowlisted, and reverts. The allowlisted router is permanently unable to deposit on behalf of any user, making the pool's liquidity-add flow unusable through any intermediary.

Both paths constitute an admin-boundary break: the pool admin's configured allowlist either fails to block unauthorized actors or incorrectly blocks authorized ones, breaking the core `addLiquidity` flow.

### Likelihood Explanation

Any pool that deploys `DepositAllowlistExtension` with a non-trivial allowlist (i.e., `allowAllDepositors` is `false`) is immediately affected. The bypass requires no special privilege — any EOA or contract can trigger it by passing an allowlisted address as `owner`. The blocking path is triggered by the standard router-based deposit pattern.

### Recommendation

Replace the unnamed first parameter with `sender` and check it instead of `owner`, mirroring `SwapAllowlistExtension`:

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

```
Setup:
  pool  = MetricOmmPool with DepositAllowlistExtension, allowAllDepositors = false
  admin calls setAllowedToDeposit(pool, alice, true)   // only alice is allowlisted
  bob   = non-allowlisted EOA

Attack (bypass):
  bob calls pool.addLiquidity(
      owner        = alice,   // allowlisted — hook checks this and passes
      salt         = 0,
      deltas       = <valid delta>,
      callbackData = <bob pays tokens in callback>,
      extensionData= ""
  )
  → beforeAddLiquidity receives (sender=bob, owner=alice)
  → allowedDepositor[pool][alice] == true  → no revert
  → bob's tokens enter the pool; alice receives LP shares
  → allowlist completely bypassed

Blocking (router):
  admin calls setAllowedToDeposit(pool, router, true)  // router is allowlisted
  user calls router.deposit(pool, user, delta)
  router calls pool.addLiquidity(owner=user, ...)
  → beforeAddLiquidity receives (sender=router, owner=user)
  → allowedDepositor[pool][user] == false → revert NotAllowedToDeposit
  → router permanently blocked despite being allowlisted
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-39)
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
```
