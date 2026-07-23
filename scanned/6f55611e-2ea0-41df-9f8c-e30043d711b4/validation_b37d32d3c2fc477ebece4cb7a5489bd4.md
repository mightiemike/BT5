### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` parameter and validates the `owner` parameter instead. Because `owner` is a caller-supplied argument to `addLiquidity`, any unprivileged address can bypass the allowlist by passing an already-allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook with two distinct addresses:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`msg.sender` is the actual transaction initiator (the `sender`); `owner` is the address that will hold the resulting position — a free parameter supplied by the caller.

`ExtensionCalling._beforeAddLiquidity` forwards both to the extension:

```solidity
// ExtensionCalling.sol lines 96-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` discards `sender` (unnamed first argument) and gates on `owner`:

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

Because `owner` is attacker-controlled, any address can call:

```
pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)
```

The extension sees `owner = allowlistedAddress`, finds it in the allowlist, and permits the deposit. The actual caller is never checked.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the first, named parameter):

```solidity
// SwapAllowlistExtension.sol lines 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The inconsistency confirms the deposit extension is checking the wrong address.

---

### Impact Explanation

A pool deployer configures `DepositAllowlistExtension` to restrict liquidity provision to a curated set of addresses (e.g., vetted market makers or institutional LPs). The bypass lets any unprivileged address:

1. **Add liquidity to a restricted pool** — directly violating the admin-configured access control.
2. **Manipulate bin state** — by depositing into specific bins, the attacker shifts `curPosInBin`, `binTotals`, and per-bin balances, altering the effective execution price for subsequent swaps.
3. **Dilute existing LP positions** — new shares are minted in the targeted bin; existing LPs' proportional claim on bin fees and residual value decreases.
4. **Grief allowlisted LPs** — the attacker can occupy `(allowlistedAddress, salt)` position keys, preventing the legitimate owner from using those keys or forcing unexpected position merges.

The position is owned by the allowlisted address (not the attacker), so the attacker bears the token cost. However, the pool's security invariant — that only approved addresses may provide liquidity — is completely broken, and the resulting bin-state manipulation can cause measurable value loss for existing LPs through dilution and price-position drift.

---

### Likelihood Explanation

- The bypass requires only a single call to `pool.addLiquidity` with `owner` set to any address already in the allowlist.
- No flash loan, oracle manipulation, or privileged access is needed.
- The allowlist of approved depositors is publicly readable (`allowedDepositor` is a public mapping), so an attacker can trivially identify a valid `owner` to supply.
- Any pool that deploys `DepositAllowlistExtension` with `allowAllDepositors = false` is affected.

---

### Recommendation

Name and check `sender` (the actual caller) instead of `owner` in `beforeAddLiquidity`, mirroring the correct pattern in `SwapAllowlistExtension`:

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

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  bob is NOT allowlisted

Attack (single transaction, no special privileges):
  bob calls pool.addLiquidity(
      owner    = alice,   // allowlisted — passes the guard
      salt     = 0,
      deltas   = { binIdxs: [0], shares: [largeAmount] },
      callbackData = "",
      extensionData = ""
  )

Result:
  - Extension checks allowedDepositor[pool][alice] → true → no revert
  - bob pays tokens via the swap callback (msg.sender = bob)
  - Position (alice, 0) is created with bob's tokens
  - Pool bin 0 state is modified by an unauthorized party
  - alice's position key is now occupied without her consent
  - Existing LPs in bin 0 are diluted
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
