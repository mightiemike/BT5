### Title
`DepositAllowlistExtension` checks position `owner` instead of actual depositor `sender`, allowing any address to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking the `owner` (position recipient) against the allowlist, but the actual token provider is `sender` (`msg.sender` of the pool call). Any unprivileged address can bypass the allowlist by calling `addLiquidity` with `owner` set to an allowlisted address.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes both `msg.sender` (as `sender`) and the caller-supplied `owner` parameter to `_beforeAddLiquidity`:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` forwards both to the extension:

```solidity
// ExtensionCalling.sol lines 95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the first argument (`sender`) and only checks `owner`:

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

`msg.sender` here is the pool (correct), but `owner` is the position recipient — not the address providing tokens. The token transfer is triggered via the swap callback from the actual caller (`sender`). An unauthorized address Bob can call `addLiquidity(owner = Alice)` where Alice is allowlisted; the check passes, Bob provides the tokens via callback, and Alice receives the position.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swapper):

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

---

### Impact Explanation

The deposit allowlist guard — the pool admin's primary mechanism for restricting who can provide liquidity — is fully bypassed by any unprivileged address. Pools configured as restricted (e.g., institutional-only, KYC-gated, or regulatory-compliant) accept deposits from any address. This breaks the core allowlist invariant and the pool admin's security model. The unauthorized depositor loses their tokens to the allowlisted `owner`'s position, but the protocol's access control is rendered inoperative.

**Severity: Medium** — broken core pool functionality (allowlist guard), no direct principal loss to existing LPs but the configured guard is completely ineffective.

---

### Likelihood Explanation

- Any address can call `addLiquidity` on any pool (no privilege required).
- The bypass requires only setting `owner` to any known allowlisted address (e.g., the pool admin, a known LP, or any address visible on-chain).
- No special tokens, flash loans, or oracle manipulation needed.
- Likelihood is **High** given the trivial trigger path.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositor and token provider) instead of `owner`:

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` configured on `beforeAddLiquidity`.
2. Admin calls `setAllowedToDeposit(pool, Alice, true)` — only Alice is authorized.
3. Bob (not allowlisted) calls `pool.addLiquidity(owner = Alice, salt, deltas, callbackData, extensionData)`.
4. Pool calls `extension.beforeAddLiquidity(sender=Bob, owner=Alice, ...)`.
5. Extension checks `allowedDepositor[pool][Alice]` → `true` → no revert.
6. Pool proceeds; Bob's callback transfers Bob's tokens into the pool; Alice receives the position shares.
7. Bob has deposited into a restricted pool despite not being allowlisted. The allowlist guard is bypassed. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
