After thoroughly reviewing the codebase — all token transfer paths, extension hook plumbing, `CallExtension`, `ExtensionCalling`, and every periphery extension — I found one valid analog.

---

### Title
`DepositAllowlistExtension` guards on `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its implementation checks the `owner` parameter (the LP-position recipient) rather than the `sender` parameter (the actual caller providing tokens). Any address not on the allowlist can deposit into a restricted pool by naming an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls `_beforeAddLiquidity(msg.sender, owner, ...)`, forwarding both the real caller (`sender = msg.sender`) and the position-owner address (`owner`, supplied by the caller): [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes both and dispatches them to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the first argument (`sender`) and checks only `owner`: [3](#0-2) 

The NatSpec and setter name both declare the intent is to gate by **depositor** (the calling entity): [4](#0-3) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual caller), not the `recipient`: [5](#0-4) 

The asymmetry confirms the `DepositAllowlistExtension` implementation is inconsistent with both its own documentation and the parallel swap-allowlist design.

---

### Impact Explanation

The deposit allowlist is an admin-configured access-control boundary. Because the guard checks the wrong actor, any unprivileged address can bypass it entirely by passing an allowlisted address as `owner`. The allowlist provides zero protection against unauthorized depositors; the admin-boundary invariant is broken.

Matching allowed impact: **Admin-boundary break — factory/pool admin access control bypassed by an unprivileged path.**

---

### Likelihood Explanation

- No special privilege is required; any EOA or contract can call `addLiquidity`.
- The allowlisted `owner` address is publicly readable on-chain (`allowedDepositor` mapping).
- The bypass is a single-call, zero-cost operation (no flash loan, no collusion required).
- Likelihood: **High**.

---

### Recommendation

Change the guard to check `sender` (the first, currently unnamed parameter) instead of `owner`:

```solidity
// Before (wrong actor):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}

// After (correct actor):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

This aligns with the NatSpec ("Gates `addLiquidity` by depositor address"), the `setAllowedToDeposit` naming, and the parallel `SwapAllowlistExtension` design.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured; `allowAllDepositors[pool] = false`.
2. Admin calls `setAllowedToDeposit(pool, Bob, true)`. Alice is **not** allowlisted.
3. Alice calls `pool.addLiquidity(owner = Bob, salt, deltas, callbackData, extensionData)`.
4. `_beforeAddLiquidity(msg.sender=Alice, owner=Bob, ...)` is dispatched to the extension.
5. Extension evaluates `allowedDepositor[pool][Bob]` → `true` → guard passes.
6. Alice's callback (`metricOmmModifyLiquidityCallback`) transfers Alice's tokens into the pool.
7. Bob receives the LP shares; Alice has deposited into a restricted pool without being allowlisted.

The deposit allowlist is fully bypassed with a single unprivileged transaction.

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-18)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
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
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```
