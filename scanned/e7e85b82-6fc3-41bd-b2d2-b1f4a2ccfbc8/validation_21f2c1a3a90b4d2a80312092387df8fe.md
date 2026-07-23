### Title
`DepositAllowlistExtension` Guards on Position `owner` Instead of Transaction `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` parameter (the actual caller of `addLiquidity`) and instead enforces the allowlist against `owner` (the position beneficiary). Because `owner` is a free caller-supplied argument, any address — including one that is explicitly not allowlisted — can bypass the guard by naming any allowlisted address as the position owner.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
//                  ^^^^^^^^^^  ^^^^^
//                  sender      owner (caller-supplied)
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` then ignores `sender` entirely (unnamed first argument) and enforces the allowlist only on `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

The sibling `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual caller):

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [4](#0-3) 

The asymmetry is the root cause: `SwapAllowlistExtension` gates the actual actor; `DepositAllowlistExtension` gates only the beneficiary label, which the actor controls freely.

The `isAllowedToDeposit` view function names its second argument `depositor`, confirming the intended semantic is to gate the actual depositing address, not the position owner: [5](#0-4) 

---

### Impact Explanation

Any address that is **not** on the allowlist can call:

```
pool.addLiquidity(allowlisted_address, salt, deltas, callbackData, extensionData)
```

The `beforeAddLiquidity` hook checks `allowedDepositor[pool][allowlisted_address]` → passes. The caller then satisfies the token-transfer callback and the position is minted under `allowlisted_address`. The pool admin's access-control boundary is fully bypassed: the allowlist no longer restricts who can deposit into the pool. This breaks the core invariant of the extension (only allowlisted addresses may add liquidity) and constitutes an admin-boundary break where an unprivileged path circumvents an admin-configured guard.

---

### Likelihood Explanation

The attack requires only:
1. Knowing any one allowlisted address (observable on-chain from prior `setAllowedToDeposit` events or successful deposits).
2. Calling `addLiquidity` with that address as `owner` and providing the required tokens via the swap callback.

No special privileges, flash loans, or oracle manipulation are needed. Any EOA or contract can execute this in a single transaction.

---

### Recommendation

Replace the ignored first argument with a named `sender` and enforce the allowlist against it, mirroring `SwapAllowlistExtension`:

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

If the intended semantic is to gate position ownership rather than the depositing caller, the extension name, NatSpec, and `setAllowedToDeposit` / `isAllowedToDeposit` API should be updated to reflect that, and the pool admin documentation must warn that the actual depositor is not gated.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured on `beforeAddLiquidity`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` and `setAllowedToDeposit(pool, bob, false)`.
3. `bob` (not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")`.
4. `beforeAddLiquidity` is invoked with `sender = bob`, `owner = alice`.
5. The check evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. `bob` satisfies the token callback; the position is minted under `alice`.
7. `bob` has successfully deposited into the pool despite being explicitly excluded from the allowlist.

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L28-30)
```text
  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
  }
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
