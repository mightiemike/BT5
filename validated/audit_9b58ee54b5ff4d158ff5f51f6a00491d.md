### Title
`DepositAllowlistExtension` gates on `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument and enforces the allowlist against `owner` (the position beneficiary). Because `MetricOmmPool.addLiquidity` lets any `msg.sender` nominate an arbitrary `owner`, an address that is not on the allowlist can call `addLiquidity(allowlisted_address, …)` and pass the guard unconditionally.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook with both the actual caller (`msg.sender` → `sender`) and the nominated position owner (`owner`):

```solidity
// MetricOmmPool.sol  line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` forwards both addresses to every registered extension:

```solidity
// ExtensionCalling.sol  line 97
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first `address` argument but leaves it unnamed and unused, then enforces the allowlist only on `owner`:

```solidity
// DepositAllowlistExtension.sol  lines 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The parallel `SwapAllowlistExtension.beforeSwap` correctly names and checks `sender`:

```solidity
// SwapAllowlistExtension.sol  lines 31-41
function beforeSwap(address sender, address, …) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    …
}
```

The structural mismatch is the root cause: the deposit guard checks the wrong address.

**Attack path**

1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only address `A` (e.g., a KYC'd LP).
2. Unauthorized address `X` calls `pool.addLiquidity(A, salt, deltas, callbackData, extensionData)`.
3. The extension receives `sender = X` (ignored) and `owner = A` (allowlisted → passes).
4. `X` satisfies the token-transfer callback; the position is minted for `A`.
5. `X` has provided liquidity to a restricted pool without being on the allowlist.

Because `removeLiquidity` enforces `msg.sender == owner`, `X` cannot reclaim the deposited tokens — but `X` has still bypassed the guard and altered pool state (bin balances, `curPosInBin`, `binTotals`) without authorization.

---

### Impact Explanation

- The deposit allowlist — the pool admin's primary mechanism for restricting who may provide liquidity — is completely ineffective. Any address can deposit by nominating any allowlisted address as `owner`.
- Pool state (bin balances, current bin position) is modified by an actor the pool admin explicitly excluded.
- If the pool is used for regulatory-compliant or permissioned liquidity (the stated purpose of the extension), the invariant "only allowlisted depositors affect pool state" is broken.
- The unauthorized depositor permanently loses the deposited tokens (they cannot call `removeLiquidity` because `msg.sender != owner`), but pool bin accounting is corrupted from the admin's perspective.

Severity: **Medium** — broken core access-control invariant with direct pool-state impact; no direct theft of LP principal, but the allowlist protection is rendered useless.

---

### Likelihood Explanation

- Exploiting requires only knowing one allowlisted address (observable on-chain via `AllowedToDepositSet` events) and calling `addLiquidity` with that address as `owner`.
- No special privileges, flash loans, or oracle manipulation are needed.
- The attacker must supply real tokens (self-funded griefing / compliance bypass), so purely profit-motivated exploitation is limited, but compliance-bypass or pool-state manipulation motivations are realistic.

---

### Recommendation

Name and check `sender` (the actual depositor) instead of `owner`, mirroring `SwapAllowlistExtension`:

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intended semantics are to restrict which *owners* may hold positions (rather than which *callers* may deposit), the NatSpec and `setAllowedToDeposit` parameter name (`depositor`) must be corrected to reflect that, and the pool admin must be made aware that any caller can create positions for allowlisted owners.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Assume: pool deployed with DepositAllowlistExtension; only `allowedLP` is allowlisted.
// Attacker = address(this), not on the allowlist.

function poc(
    IMetricOmmPool pool,
    address allowedLP,          // any address visible via AllowedToDepositSet event
    LiquidityDelta calldata deltas,
    bytes calldata callbackData
) external {
    // Extension checks owner == allowedLP → passes.
    // msg.sender (attacker) is never checked.
    pool.addLiquidity(
        allowedLP,   // owner: allowlisted → guard passes
        0,           // salt
        deltas,
        callbackData,
        ""
    );
    // Pool state is now modified by an address the admin explicitly excluded.
    // Attacker's tokens are locked in allowedLP's position; allowedLP can withdraw them.
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
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
