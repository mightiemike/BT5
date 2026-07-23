### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook, however, silently discards the `sender` argument (the actual caller who pays tokens) and instead validates the `owner` argument (the LP-share recipient). Any non-allowlisted address can therefore bypass the restriction by naming an allowlisted address as `owner`, depositing into a pool they are explicitly excluded from.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two address arguments from the pool: `sender` (position 0, the `msg.sender` of `addLiquidity`) and `owner` (position 1, the LP-share recipient). The hook discards `sender` entirely and checks only `owner`:

```solidity
// DepositAllowlistExtension.sol L32-42
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
``` [1](#0-0) 

The pool passes `msg.sender` as `sender` and the caller-supplied `owner` parameter as `owner`:

```solidity
// ExtensionCalling.sol L88-99
function _beforeAddLiquidity(
    address sender,   // = msg.sender of addLiquidity()
    address owner,    // = caller-supplied LP-share recipient
    ...
) internal {
    _callExtensionsInOrder(
        BEFORE_ADD_LIQUIDITY_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
}
``` [2](#0-1) 

Because `owner` is a free parameter in `addLiquidity`, any caller can set it to any address. The allowlist check therefore tests the wrong identity: it tests who *receives* the shares, not who *pays* the tokens and initiates the deposit.

The contract's own NatSpec, admin setter, and view function all use the word **depositor** — meaning the caller — making the intent unambiguous:

```solidity
// DepositAllowlistExtension.sol L18-29
function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) { ... }
function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) { ... }
``` [3](#0-2) 

The companion `SwapAllowlistExtension` correctly checks `sender` (the actual swapper), confirming the deposit extension's check is wrong by comparison:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [4](#0-3) 

---

### Impact Explanation

A pool admin deploys `DepositAllowlistExtension` to restrict which addresses may add liquidity (e.g., a private LP pool, a KYC-gated pool, or a pool in a sensitive state where uncontrolled liquidity addition could harm existing LPs). The guard is silently inoperative: any non-allowlisted address can call `pool.addLiquidity(owner = <any_allowlisted_address>, ...)`, pass the hook, and deposit tokens into the restricted pool. The LP shares are credited to the named allowlisted address, but the deposit itself — the state change the admin intended to block — succeeds unconditionally. This breaks the core pool invariant that the allowlist enforces and constitutes an admin-boundary break by an unprivileged path.

---

### Likelihood Explanation

The exploit requires no special permissions, no oracle manipulation, and no reentrancy. Any externally-owned account can observe the allowlist (it is public), pick any allowlisted address as `owner`, and call `addLiquidity` directly on the pool. The attacker pays the deposited tokens (which go into the pool) and the allowlisted address receives LP shares it did not request. The attacker's cost is the deposited capital; the benefit is unrestricted access to a pool the admin intended to gate.

---

### Recommendation

Replace the `owner` check with a `sender` check in `beforeAddLiquidity`:

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap` and aligns with the contract's own NatSpec, setter, and view-function naming.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured; only `alice` is allowlisted (`allowedDepositor[pool][alice] = true`).
2. `bob` (not allowlisted) calls:
   ```solidity
   pool.addLiquidity(
       owner = alice,   // allowlisted address used as a pass
       salt  = 99,
       deltas = ...,
       amount0Max = X,
       amount1Max = Y,
       extensionData = ""
   );
   ```
3. The pool calls `extension.beforeAddLiquidity(bob, alice, 99, ...)`.
4. The hook evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
5. `bob`'s tokens are transferred into the pool; LP shares at `(alice, 99)` are minted.
6. `bob` has successfully deposited into a pool the admin intended to restrict him from, bypassing the allowlist entirely.

### Citations

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
