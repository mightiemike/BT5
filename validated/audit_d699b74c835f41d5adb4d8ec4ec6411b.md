### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its admin-facing setter is named `setAllowedToDeposit` and its mapping is named `allowedDepositor`. However, the `beforeAddLiquidity` hook checks the position `owner` argument instead of the `sender` argument (the actual caller who provides tokens via callback). Any unprivileged address can bypass the allowlist entirely by specifying any allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`msg.sender` is the **depositor** — the address that calls `addLiquidity` and must satisfy the callback to transfer tokens into the pool. `owner` is the **position beneficiary** — the address that will hold the LP shares and can later call `removeLiquidity`.

`ExtensionCalling._beforeAddLiquidity` forwards both as the first two positional arguments:

```solidity
// ExtensionCalling.sol lines 95-98
_callExtensionsInOrder(
  BEFORE_ADD_LIQUIDITY_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
);
```

`DepositAllowlistExtension.beforeAddLiquidity` silently discards `sender` (first argument, unnamed) and checks only `owner`:

```solidity
// DepositAllowlistExtension.sol lines 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The guard should read `allowedDepositor[msg.sender][sender]` but reads `allowedDepositor[msg.sender][owner]`. Because `owner` is a free caller-supplied parameter with no on-chain constraint tying it to `msg.sender`, any caller can pass any allowlisted address as `owner` and the check succeeds unconditionally.

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for creating a permissioned liquidity pool (e.g., KYC-gated, institutional-only, or whitelist-only pools). With this bug the guard is completely inoperative:

1. **Allowlist bypass**: Any address — including addresses the pool admin explicitly excluded — can call `pool.addLiquidity(allowlistedOwner, ...)`, pass the guard, and deposit tokens. The pool admin's access-control configuration is silently nullified.
2. **Forced position creation**: The depositor provides tokens via the swap callback but the LP shares are minted to `owner`. `removeLiquidity` enforces `msg.sender == owner`, so the depositor cannot recover the tokens; only `owner` can withdraw. An attacker can therefore force tokens into any allowlisted address's position without that address's consent.
3. **Admin-boundary break**: The pool admin's restriction — which address may deposit — is bypassed by an unprivileged path with no special role or privilege required.

---

### Likelihood Explanation

Exploitation requires only knowledge of one allowlisted owner address (readable from `allowedDepositor` public mapping or emitted `AllowedToDepositSet` events) and the ability to call `addLiquidity`. No privileged role, flash loan, or oracle manipulation is needed. Any user of the protocol can trigger this on any pool that deploys `DepositAllowlistExtension` in non-allow-all mode.

---

### Recommendation

Replace the unnamed first parameter with a named `sender` variable and check it instead of `owner`:

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the pool's `msg.sender`) against `allowedSwapper`.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only `alice`:
   ```
   extension.setAllowedToDeposit(pool, alice, true);
   ```
2. `bob` (not allowlisted) calls:
   ```solidity
   pool.addLiquidity(alice, salt, deltas, callbackData, extensionData);
   ```
3. Inside `beforeAddLiquidity`, `msg.sender == pool`, `owner == alice`. The check `allowedDepositor[pool][alice]` returns `true` → no revert.
4. `bob` satisfies the token callback, transferring his tokens into the pool. LP shares are minted to `alice`.
5. `alice` calls `removeLiquidity(alice, ...)` and withdraws `bob`'s tokens. `bob` has no recourse.
6. The pool admin's deposit restriction was never enforced against `bob`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L205-207)
```text
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
```
