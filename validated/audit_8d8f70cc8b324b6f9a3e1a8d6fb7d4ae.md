The key code is already found. Let me verify the exact call chain to confirm the actor binding.

The call chain is fully traced. The vulnerability is real.

---

### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing non-allowlisted callers to deposit — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument and gates on `owner` instead. Because `addLiquidity` accepts a caller-controlled `owner` parameter, any non-allowlisted address can pass the allowlist check by supplying an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes both `msg.sender` (the actual caller) and the caller-supplied `owner` (the LP-share recipient) to the before-hook:

```
MetricOmmPool.addLiquidity(owner=victim)          // msg.sender = attacker
  → _beforeAddLiquidity(sender=attacker, owner=victim, ...)
    → abi.encodeCall(beforeAddLiquidity, (attacker, victim, ...))
      → DepositAllowlistExtension.beforeAddLiquidity(address /*unnamed*/, address owner=victim, ...)
``` [1](#0-0) 

Inside the extension, the `sender` parameter is unnamed and therefore silently discarded. The guard reads:

```solidity
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
}
``` [2](#0-1) 

`msg.sender` here is the **pool** (correct key for the pool namespace), but `owner` is the LP-share recipient — not the address that pays tokens and initiates the call. The actual depositor (`sender`) is never consulted.

The contract's own NatDoc states: *"Gates `addLiquidity` by depositor address, per pool."* The depositor is the `sender`, not the `owner`. [3](#0-2) 

---

### Impact Explanation

A non-allowlisted attacker can call `pool.addLiquidity(owner=allowlisted_victim, ...)` and the check `allowedDepositor[pool][victim]` returns `true`, so the deposit proceeds. The attacker pays the tokens (callback is invoked on `msg.sender=attacker`) and LP shares are minted to `victim`. The attacker bypasses the curated-pool access control entirely, adding liquidity to a pool that was explicitly restricted to approved depositors. This breaks the core invariant of the `DepositAllowlistExtension` — that only allowlisted addresses may deposit tokens — and allows unauthorized manipulation of bin balances and share accounting on restricted pools.

---

### Likelihood Explanation

The `owner` parameter is freely caller-controlled with no validation in `addLiquidity`. Any address that knows an allowlisted address (e.g., from on-chain events emitted by `setAllowedToDeposit`) can exploit this immediately. No privileged access, no special setup, and no non-standard token behavior is required. [4](#0-3) 

---

### Recommendation

Name and use the `sender` parameter instead of `owner` in `beforeAddLiquidity`:

```solidity
// Before (buggy):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// After (fixed):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
``` [2](#0-1) 

---

### Proof of Concept

```solidity
// Foundry integration test
function test_nonAllowlistedAttackerBypassesDepositAllowlist() public {
    address attacker = makeAddr("attacker");
    address victim   = makeAddr("victim");   // allowlisted

    // Pool admin allowlists victim, NOT attacker
    depositExtension.setAllowedToDeposit(address(pool), victim, true);
    assertFalse(depositExtension.isAllowedToDeposit(address(pool), attacker));

    // Fund and approve attacker
    token0.mint(attacker, 1e18);
    token1.mint(attacker, 1e18);
    vm.prank(attacker);
    token0.approve(address(pool), type(uint256).max);
    vm.prank(attacker);
    token1.approve(address(pool), type(uint256).max);

    // Attacker calls addLiquidity with owner=victim (allowlisted)
    // Expected: revert NotAllowedToDeposit — Actual: succeeds
    vm.prank(attacker);
    pool.addLiquidity(victim, 0, deltas, callbackData, "");

    // Allowlist was bypassed: attacker (not allowlisted) deposited tokens
    assertFalse(depositExtension.isAllowedToDeposit(address(pool), attacker)); // still false
    // LP shares minted to victim, tokens taken from attacker
}
```

The call path is: `attacker → pool.addLiquidity(owner=victim)` → `_beforeAddLiquidity(sender=attacker, owner=victim)` → `DepositAllowlistExtension.beforeAddLiquidity(/*sender discarded*/, owner=victim)` → `allowedDepositor[pool][victim]=true` → deposit succeeds. [5](#0-4) [2](#0-1)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-13)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
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
