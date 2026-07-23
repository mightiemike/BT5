### Title
DepositAllowlistExtension Checks LP Recipient (`owner`) Instead of Actual Depositor (`sender`), Allowing Unauthorized Deposits Into Curated Pools — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently ignores the `sender` parameter (the address that actually calls `pool.addLiquidity` and provides tokens) and instead checks the `owner` parameter (the address that receives LP shares) against the allowlist. Because `addLiquidity` imposes no constraint that `msg.sender == owner`, any unauthorized user can bypass the deposit gate by supplying an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` that is independent of `msg.sender`:

```solidity
// MetricOmmPool.sol
function addLiquidity(
    address owner,          // LP-share recipient — caller-controlled
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) ...
```

It then forwards both actors to the extension hook:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
//                  ^^sender^^  ^^owner^^
```

Inside `ExtensionCalling._beforeAddLiquidity`, the call is encoded as:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

The extension receives `sender` as its first `address` argument and `owner` as its second. `DepositAllowlistExtension` discards `sender` entirely and checks only `owner`:

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(
    address,        // ← sender silently ignored
    address owner,  // ← LP recipient checked instead
    uint80,
    LiquidityDelta calldata,
    bytes calldata
) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This is the wrong actor. The allowlist is named `setAllowedToDeposit` / `isAllowedToDeposit`, signalling intent to gate the depositor, not the LP-share recipient. The `SwapAllowlistExtension` correctly checks `sender` (the actual swapper) for the analogous swap gate, making the inconsistency clear:

```solidity
// SwapAllowlistExtension.sol — correct pattern
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

---

### Impact Explanation

An unauthorized address (not on the allowlist) can deposit tokens into a curated pool by setting `owner` to any allowlisted address. The extension sees `allowedDepositor[pool][allowlistedAddress] == true` and permits the call. The unauthorized user provides the tokens; LP shares are minted to the allowlisted address.

Concrete consequences:
- **Admin-boundary break**: the pool admin's curation policy (KYC, whitelist, institutional-only) is bypassed by any public caller who knows one allowlisted address.
- **Pool-state manipulation**: the unauthorized depositor shifts bin balances and `curPosInBin`, affecting the marginal price seen by all subsequent swappers and LPs.
- **Collusion path to full bypass**: if the unauthorized user controls or colludes with an allowlisted address, they call `addLiquidity(colludingAllowlistedAddr, ...)`, then the allowlisted address calls `removeLiquidity`, returning the tokens. The net effect is a complete round-trip through the curated pool with no allowlist enforcement.

---

### Likelihood Explanation

Allowlisted addresses are typically discoverable on-chain (emitted in `AllowedToDepositSet` events or visible in public storage). Any user who can read chain state can identify an allowlisted address and craft the bypass call. No special privilege or oracle manipulation is required — only a standard `addLiquidity` call with a crafted `owner` argument.

---

### Recommendation

Check `sender` (the actual token depositor) instead of `owner` (the LP-share recipient), mirroring the correct pattern in `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(
    address sender,   // ← check this actor
    address,          // owner — not relevant for deposit gating
    uint80,
    LiquidityDelta calldata,
    bytes calldata
) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` attached to `beforeAddLiquidity`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. `bob` is not allowlisted.
3. `bob` calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. Pool calls `_beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)`.
5. Extension evaluates `allowedDepositor[pool][alice] == true` → no revert.
6. `LiquidityLib.addLiquidity` executes: `bob`'s tokens enter the pool; LP shares are minted to `alice`.
7. `bob` has deposited into the curated pool without being on the allowlist, violating the admin's curation policy.
8. If `alice` is controlled by or cooperates with `bob`, `alice` calls `removeLiquidity` and returns the tokens to `bob`, completing a full allowlist bypass.

---

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
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
