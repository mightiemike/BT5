Let me look at the pool's `addLiquidity` implementation to understand the `sender` vs `owner` distinction, and also check the router to understand how these are set.

### Title
`DepositAllowlistExtension` Validates `owner` Instead of `sender`, Allowing Any Unlisted Address to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` parameter (the actual token-providing caller) and checks `owner` (the LP-position recipient) instead. Any unlisted address can bypass the deposit allowlist by calling `addLiquidity` with `owner` set to an allowlisted address.

---

### Finding Description

The contract is documented as *"Gates `addLiquidity` by depositor address"* and its admin setter names the gated entity `depositor`: [1](#0-0) 

However, the hook implementation silently discards the first argument (`sender`) and checks `owner` instead: [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` passes both `sender` (the `msg.sender` of `addLiquidity`) and `owner` (the LP-position recipient) to the hook: [3](#0-2) 

The `SwapAllowlistExtension` correctly checks `sender` for the analogous swap gate: [4](#0-3) 

The entity mismatch in `DepositAllowlistExtension` is the direct analog of the xToken M-06 bug: just as xToken's `approve` tracked shares while `transferFrom` consumed rebalanced amounts (wrong unit, same identifier), here the guard tracks `owner` while the actual capital flow comes from `sender` (wrong identifier, same hook).

---

### Impact Explanation

An unlisted `sender` can deposit tokens into a permissioned pool by supplying any allowlisted address as `owner`. The check `allowedDepositor[msg.sender][owner]` passes because `owner` is allowlisted, even though `sender` is not. The unlisted address provides the tokens; the LP position is minted to the allowlisted `owner`. The deposit allowlist — the pool admin's primary access-control mechanism for capital inflows — is fully bypassed for any caller who knows one allowlisted address (a public mapping). For regulated or KYC-gated pools this breaks the core invariant that only vetted addresses may supply liquidity.

---

### Likelihood Explanation

Medium. The allowlist mapping `allowedDepositor` is public, so any on-chain observer can enumerate allowlisted addresses. No special privilege is required beyond knowing one such address. The attacker forfeits the LP position (it goes to `owner`), but the bypass itself is unconditional and requires no flash loan, callback, or multi-step setup.

---

### Recommendation

Check `sender` (the actual token provider) instead of `owner`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
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

This aligns with the `setAllowedToDeposit` parameter name (`depositor`) and mirrors the correct pattern used in `SwapAllowlistExtension` for `sender`.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension`. Address `A` is allowlisted via `setAllowedToDeposit(pool, A, true)`.
2. Unlisted address `B` calls `pool.addLiquidity(..., owner = A, ...)`.
3. The pool calls `_beforeAddLiquidity(sender=B, owner=A, ...)`, which calls `beforeAddLiquidity(B, A, ...)` on the extension.
4. The hook evaluates `allowedDepositor[pool][A]` → `true`. No revert.
5. `B`'s tokens are deposited into the pool; the LP position is minted to `A`.
6. `B` has bypassed the deposit allowlist. The pool admin's access control over capital inflows is defeated.

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-21)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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
