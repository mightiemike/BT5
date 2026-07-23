### Title
`DepositAllowlistExtension` gates on `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument and checks the LP position `owner` instead. Because `owner` is a caller-supplied parameter to `addLiquidity`, any address — including one that is not on the allowlist — can deposit into a restricted pool by naming an allowlisted address as `owner`.

---

### Finding Description

The pool calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
//                  ^^^^^^^^^^  ^^^^^
//                  sender      LP position owner (caller-controlled)
``` [1](#0-0) 

Inside the extension, the first parameter (`sender`) is unnamed and discarded; only `owner` is checked:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [2](#0-1) 

Because `owner` is a free parameter supplied by the caller of `addLiquidity`, any non-allowlisted address can pass the guard by setting `owner` to any address that the pool admin has already allowlisted.

Compare with `SwapAllowlistExtension`, which correctly checks `sender` (the actual caller):

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [3](#0-2) 

The inconsistency confirms the deposit extension is checking the wrong actor.

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may provide liquidity to a pool (e.g., KYC/compliance-gated pools, institutional-only pools). With this bug:

- Any non-allowlisted address can call `addLiquidity(owner = <allowlisted_addr>, ...)`, pay the token callback, and successfully deposit into a restricted pool.
- The LP shares are credited to the allowlisted `owner`, who can then call `removeLiquidity` to recover the tokens — effectively receiving a free transfer from the attacker.
- The pool admin's access control invariant is broken: the allowlist no longer restricts who can deposit.

This qualifies as an **admin-boundary break**: a pool admin-configured guard is bypassed by an unprivileged path.

---

### Likelihood Explanation

- Requires only knowing one allowlisted address (observable on-chain via `AllowedToDepositSet` events or `allowedDepositor` public mapping).
- No special permissions, flash loans, or oracle manipulation needed.
- The attacker bears a token cost, but the bypass itself is trivially reachable by any EOA or contract.

---

### Recommendation

Check `sender` (the actual caller of `addLiquidity`) instead of `owner`:

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

Update `setAllowedToDeposit` / `isAllowedToDeposit` documentation to clarify that the allowlist gates the **caller** of `addLiquidity`, not the LP position owner. [4](#0-3) 

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension`; pool admin calls `setAllowedToDeposit(pool, alice, true)`.
2. Bob (not allowlisted) calls `pool.addLiquidity(owner=alice, salt=0, deltas=..., callbackData=..., extensionData=...)`.
3. Pool calls `extension.beforeAddLiquidity(bob, alice, ...)`.
4. Extension checks `allowedDepositor[pool][alice]` → `true` → no revert.
5. Liquidity is added; Alice receives LP shares; Bob's callback pays the tokens.
6. Alice calls `pool.removeLiquidity(owner=alice, salt=0, ...)` and recovers the tokens.

Bob has successfully deposited into a pool he is not authorized to access, and Alice has received a free token transfer. The pool admin's deposit allowlist is entirely ineffective against this pattern.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-29)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }

  function setAllowAllDepositors(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllDepositors[pool_] = allowed;
    emit AllowAllDepositorsSet(pool_, allowed);
  }

  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
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
