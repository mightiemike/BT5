### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual token-paying caller) and gates only on `owner` (the position recipient). Because `MetricOmmPool.addLiquidity` imposes no `msg.sender == owner` constraint, any unprivileged address can call `addLiquidity(owner = allowlistedAddress, …)`, pass the allowlist check, and inject liquidity into a restricted pool.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two address arguments: `sender` (the direct caller of `pool.addLiquidity`, i.e., the entity that will pay tokens through the callback) and `owner` (the address that will own the resulting LP position). The implementation leaves `sender` unnamed and therefore completely ignored, checking only `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` with no requirement that `msg.sender == owner` (unlike `removeLiquidity`, which does enforce that constraint):

```solidity
// metric-core/contracts/MetricOmmPool.sol
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) ...
``` [2](#0-1) 

The pool then calls `_beforeAddLiquidity(msg.sender, owner, …)`, forwarding both addresses to the extension: [3](#0-2) 

Because the extension ignores `sender` and only validates `owner`, an attacker who supplies an allowlisted address as `owner` will always pass the guard, regardless of who `msg.sender` is.

**Structural analog to the external report:** In the Deriverse bug, `client-community` was passed as `None`, causing `fee_rate` to be zero and the fee guard to be silently skipped. Here, `sender` is passed as an unnamed (discarded) parameter — effectively `None` — causing the allowlist guard to evaluate the wrong address and silently pass for any caller.

---

### Impact Explanation

A pool that deploys `DepositAllowlistExtension` intends to restrict which addresses may provide liquidity (e.g., for regulatory compliance, curated LP sets, or controlled pool bootstrapping). The bypass allows:

1. **Unauthorized liquidity injection**: Any address, regardless of allowlist status, can add liquidity to a restricted pool by specifying an allowlisted `owner`.
2. **Broken access-control invariant**: The pool admin's configured allowlist is rendered ineffective — an unprivileged path bypasses the admin-set guard entirely.
3. **Forced position creation**: The allowlisted `owner` receives an LP position they did not initiate; they can remove it, but the pool's composition and bin state are altered without their consent.
4. **Pool state manipulation**: An attacker can shift liquidity distribution across bins in a restricted pool, affecting price impact and fee accrual for legitimate LPs.

This matches the allowed impact gate: *"Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path"* and *"Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows."*

---

### Likelihood Explanation

- **No special privilege required**: Any EOA or contract can call `pool.addLiquidity` directly.
- **Trivially constructable**: The attacker only needs to know one allowlisted address (e.g., from on-chain events emitted by `setAllowedToDeposit`).
- **No economic cost barrier**: The attacker pays tokens that are credited to the allowlisted owner; the attacker loses those tokens, but the bypass itself is unconditional.
- **Affects every pool using this extension**: The bug is in the shared singleton extension contract, so all pools that register `DepositAllowlistExtension` are affected simultaneously.

---

### Recommendation

Replace the unnamed first parameter with `sender` and gate on the actual caller, not the position owner:

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`, which gates on `sender` (the direct caller of `swap`): [4](#0-3) 

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
//   - pool has DepositAllowlistExtension registered for beforeAddLiquidity
//   - allowedDepositor[pool][alice] = true  (alice is allowlisted)
//   - bob is NOT on the allowlist

// Attack:
// 1. Bob calls pool.addLiquidity with owner = alice
pool.addLiquidity(
    alice,          // owner: allowlisted → extension check passes
    0,              // salt
    deltas,         // liquidity to add
    callbackData,   // bob pays tokens via callback
    extensionData
);
// Result: extension checks allowedDepositor[pool][alice] == true → no revert
// Bob's tokens enter the pool; alice receives the LP position
// The deposit allowlist is completely bypassed
```

The pool calls `_beforeAddLiquidity(msg.sender=bob, owner=alice, …)`. The extension receives `sender=bob` (unnamed, discarded) and `owner=alice` (checked). Since `alice` is allowlisted, the guard passes. Bob has successfully deposited into a pool he is not authorized to access. [1](#0-0) [3](#0-2)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L211-224)
```text
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
  }

  // ============ External: swap ============

  /// @inheritdoc IMetricOmmPoolActions
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
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
