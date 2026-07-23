### Title
`DepositAllowlistExtension` Checks LP Position Owner Instead of Actual Depositor, Allowing Complete Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the `owner` parameter (the LP position recipient) rather than the `sender` parameter (the address actually calling `addLiquidity` and paying tokens). Because `owner` is a free caller-supplied argument, any address not on the allowlist can bypass the guard entirely by naming an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` forwards two distinct addresses into the `beforeAddLiquidity` hook:

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
//                  ^^^^^^^^^^  ^^^^^
//                  sender      owner (caller-supplied)
```

`sender` (`msg.sender`) is the address that actually calls `addLiquidity` and must satisfy the swap-callback to pay tokens into the pool. `owner` is an arbitrary address the caller provides to designate who receives the LP shares.

`DepositAllowlistExtension.beforeAddLiquidity` silently drops `sender` and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The first parameter (`sender`) is unnamed and never read. The guard therefore asks "is this LP-position recipient allowlisted?" rather than "is the depositor allowlisted?" — the exact opposite of the stated invariant ("Gates `addLiquidity` by depositor address").

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly reads `sender`:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

The asymmetry confirms the deposit hook is checking the wrong field.

---

### Impact Explanation

The deposit allowlist guard is rendered completely inoperative. Any address — regardless of allowlist status — can add liquidity to a restricted pool by supplying any allowlisted address as `owner`. Concrete consequences:

1. **Allowlist bypass**: Unauthorized parties can deposit into pools the admin intended to restrict (e.g., KYC-gated, whitelist-only, or manipulation-resistant pools).
2. **Pool-state manipulation**: An attacker can add liquidity at chosen bins to shift `curPosInBin` / `curBinIdx`, then execute swaps at the distorted price, extracting value from existing LPs.
3. **Forced LP positions**: The attacker pays tokens and creates LP shares attributed to an allowlisted address that never consented, potentially locking that address into an unwanted position.

The broken invariant falls under **Admin-boundary break** (an unprivileged path bypasses a pool-admin-configured access control) and **Broken core pool functionality** (the allowlist hook does not enforce its stated gate).

---

### Likelihood Explanation

Exploitation requires no special privilege, no flash loan, and no complex setup. Any EOA or contract can call `pool.addLiquidity(allowlistedAddress, salt, deltas, ...)`. The allowlisted address is publicly readable from `allowedDepositor`. Likelihood is **High**.

---

### Recommendation

Replace the `owner` check with a `sender` check, mirroring the correct pattern in `SwapAllowlistExtension`:

```solidity
// BEFORE (wrong — checks LP recipient, not depositor)
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// AFTER (correct — checks the actual depositor)
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
```

---

### Proof of Concept

```
Setup:
  pool  = MetricOmmPool with DepositAllowlistExtension
  admin sets allowedDepositor[pool][trustedLP] = true
  attacker is NOT on the allowlist

Attack:
  attacker calls pool.addLiquidity(
      owner        = trustedLP,   // allowlisted — passes the guard
      salt         = 0,
      deltas       = <chosen bins>,
      callbackData = ...,
      extensionData= ""
  )

Hook execution:
  beforeAddLiquidity(sender=attacker, owner=trustedLP, ...)
  → allowedDepositor[pool][trustedLP] == true  ✓ guard passes

Result:
  - attacker pays tokens via metricOmmSwapCallback
  - LP shares minted to trustedLP
  - attacker has added liquidity to a restricted pool without being allowlisted
  - attacker can repeat with different bins to manipulate pool price state
    and subsequently swap at the distorted price for profit
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
