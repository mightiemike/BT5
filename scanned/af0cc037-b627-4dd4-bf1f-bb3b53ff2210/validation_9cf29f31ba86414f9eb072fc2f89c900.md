### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook receives two actor addresses — `sender` (the actual `msg.sender` of `addLiquidity`, who pays tokens via callback) and `owner` (the caller-supplied LP-position recipient). The hook silently discards `sender` and only checks `owner`. Because `owner` is a free parameter in `addLiquidity`, any address not on the allowlist can deposit into a restricted pool by nominating an allowlisted address as `owner`.

---

### Finding Description

**Hook parameter mismatch**

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` and passes both `msg.sender` and `owner` into the extension chain:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both:

```solidity
// ExtensionCalling.sol lines 95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` then ignores the first argument entirely (it is unnamed) and gates only on `owner`:

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

`msg.sender` inside the extension is the pool (correct for the pool-identity check), but `owner` is the LP-position recipient — an address the caller chose freely. The actual token depositor (`sender`, i.e. the original `msg.sender` of `addLiquidity`) is never inspected.

**Contrast with `SwapAllowlistExtension`**

`SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the first parameter, the actual swapper):

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

The deposit extension applies the same pattern to the wrong field.

**`removeLiquidity` is not affected**

`removeLiquidity` enforces `msg.sender == owner` before calling extensions, so `sender` and `owner` are always the same address there. The asymmetry exists only in `addLiquidity`.

---

### Impact Explanation

Any address not on the allowlist can deposit into a pool that has `DepositAllowlistExtension` configured by:

1. Picking any allowlisted address `alice` as `owner`.
2. Calling `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
3. The hook checks `allowedDepositor[pool][alice]` → passes.
4. The caller pays tokens via the swap callback; `alice` receives LP shares.

Consequences:
- The allowlist guard is completely ineffective — every pool using `DepositAllowlistExtension` is open to any depositor.
- Restricted pools (e.g., KYC-gated, institutional-only) can be entered by arbitrary addresses.
- LP shares are minted to an allowlisted address without their consent, altering their position and exposure.
- An attacker can concentrate liquidity in a restricted pool to influence price-bin state, affecting swap outcomes and LP returns for legitimate participants.

---

### Likelihood Explanation

The bypass requires no special privilege, no flash loan, and no oracle manipulation. Any EOA or contract can call `addLiquidity` with an arbitrary `owner`. The allowlisted address need not cooperate. The attack is trivially repeatable and costs only gas plus the deposited tokens (which the attacker controls).

---

### Recommendation

Check `sender` (the actual depositor) instead of `owner` (the LP-position recipient):

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

If the intent is to gate both the depositor and the LP-position recipient, both should be checked.

---

### Proof of Concept

Setup:
- Pool `P` has `DepositAllowlistExtension` with `allowedDepositor[P][alice] = true`.
- `bob` is not on the allowlist.

Attack:
```
bob calls P.addLiquidity(
    owner        = alice,   // allowlisted — passes the guard
    salt         = 0,
    deltas       = <valid deltas>,
    callbackData = <bob pays tokens>,
    extensionData = ""
)
```

Execution trace:
1. `_beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)` is called.
2. Extension receives `(bob, alice, ...)` but checks only `allowedDepositor[P][alice]` → `true`.
3. Hook returns success selector; no revert.
4. `LiquidityLib.addLiquidity` mints shares to `alice`.
5. Pool calls `bob.metricOmmSwapCallback(...)` — bob pays tokens.

Result: `bob` has deposited into a restricted pool; the allowlist provided zero protection.

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
