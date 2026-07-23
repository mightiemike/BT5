### Title
`DepositAllowlistExtension` gates on `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` parameter (the actual token payer) and checks `owner` (the LP position owner, a freely caller-supplied argument) against the allowlist. Because `owner` is an unconstrained input to `MetricOmmPool.addLiquidity`, any non-allowlisted address can bypass the deposit gate by nominating an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts `owner` as a plain caller-supplied argument and passes `msg.sender` as `sender` to the extension hook:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`DepositAllowlistExtension.beforeAddLiquidity` receives both values but discards `sender` (unnamed first parameter) and checks only `owner`:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`msg.sender` inside the extension is the pool (correct for pool-identity gating), and `owner` is whatever the original caller passed. Since `owner` is not validated against the actual payer anywhere in the pool or the liquidity adder, the allowlist check is trivially satisfied by any caller who supplies an already-allowlisted address as `owner`.

The `MetricOmmPoolLiquidityAdder` makes this reachable from the standard periphery path: `addLiquidityExactShares(pool, owner, ...)` stores `msg.sender` as the payer in transient context and forwards the caller-chosen `owner` directly to the pool, so the extension sees an allowlisted `owner` while the actual token pull comes from the non-allowlisted `msg.sender`.

The `SwapAllowlistExtension` correctly checks `sender` (the actual caller), confirming the asymmetry is a defect specific to the deposit extension.

---

### Impact Explanation

The deposit allowlist is the only on-chain mechanism preventing unauthorized addresses from providing liquidity to a restricted pool. With the guard checking the wrong identity:

1. A non-allowlisted address calls `pool.addLiquidity(allowlistedAddress, salt, deltas, ...)` directly or via `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, allowlistedAddress, ...)`.
2. The extension check passes because `allowedDepositor[pool][allowlistedAddress]` is `true`.
3. The non-allowlisted address pays the tokens through the callback; LP shares are minted to `allowlistedAddress`.
4. The allowlisted address now holds LP exposure it never authorized.

The allowlisted address can remove the position, but between deposit and removal the pool's value may move adversely, causing a real loss to the allowlisted LP. Additionally, the pool admin's deposit restriction — the sole guard against unauthorized capital entering the pool — is fully defeated by any unprivileged caller at zero protocol cost.

---

### Likelihood Explanation

The bypass requires no special privilege, no flash loan, and no oracle manipulation. Any EOA or contract can call `addLiquidity` with an arbitrary `owner`. The only cost to the attacker is the token amount deposited, which is recoverable if the attacker controls the allowlisted address or is simply griefing. The path is reachable both directly on the pool and through the standard `MetricOmmPoolLiquidityAdder` periphery contract.

---

### Recommendation

Replace the unnamed (ignored) first parameter with `sender` and gate on it instead of `owner`:

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`, which checks `sender` (the actual swapper) rather than `recipient`.

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][ALICE] = true   // ALICE is allowlisted
  BOB is NOT allowlisted

Attack (BOB calls directly):
  pool.addLiquidity(
      owner        = ALICE,   // allowlisted → check passes
      salt         = 0,
      deltas       = <shares>,
      callbackData = <pay BOB's tokens>,
      extensionData = ""
  )

Result:
  Extension: allowedDepositor[pool][ALICE] == true → no revert
  LiquidityLib mints shares to (ALICE, 0) position key
  BOB's tokens are pulled via callback
  ALICE holds LP shares she never authorized
  BOB has bypassed the deposit allowlist
```

The same path works through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, ALICE, ...)` where `msg.sender` = BOB is stored as payer but `owner` = ALICE is what the extension checks. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
