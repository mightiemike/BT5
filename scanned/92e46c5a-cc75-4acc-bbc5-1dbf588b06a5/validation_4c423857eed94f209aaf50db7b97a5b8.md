### Title
`DepositAllowlistExtension.beforeAddLiquidity` gates on `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is the production extension that curated pools use to restrict who may add liquidity. Its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual caller of `pool.addLiquidity`) and checks only `owner` (the position-owner address, which is a free caller-supplied parameter). Because `MetricOmmPool.addLiquidity` imposes no constraint on `owner`, any non-allowlisted address can bypass the gate by passing an allowlisted address as `owner`.

---

### Finding Description

**Step 1 — The hook ignores `sender` and checks `owner`.** [1](#0-0) 

The first parameter (`sender`) is unnamed and never read. The allowlist lookup is keyed on `owner`, which is the second parameter.

**Step 2 — The pool passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner`.** [2](#0-1) 

`owner` is a free parameter accepted from any external caller with no identity constraint.

**Step 3 — `ExtensionCalling._beforeAddLiquidity` forwards both values verbatim.** [3](#0-2) 

**Step 4 — `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (owner-override overload) only validates `owner != address(0)`.** [4](#0-3) 

`_validateOwner` is a zero-address check only; it does not require `owner == msg.sender`.

**Bypass path (direct pool call):**

```
attacker (not allowlisted)
  → pool.addLiquidity(owner = allowlistedAlice, ...)
      → _beforeAddLiquidity(sender = attacker, owner = allowlistedAlice, ...)
          → DepositAllowlistExtension.beforeAddLiquidity(_, allowlistedAlice, ...)
              allowedDepositor[pool][allowlistedAlice] == true  ← PASSES
  → LiquidityLib mints shares to allowlistedAlice
  → pool calls attacker.metricOmmModifyLiquidityCallback(...)
      attacker pays tokens
```

The extension never inspects `sender = attacker`. The allowlist check passes because `owner` is allowlisted. The attacker's contract pays the tokens via the callback, pool state is mutated, and LP shares are minted to `allowlistedAlice`.

**Bypass path (LiquidityAdder weighted probe):** [5](#0-4) 

Both the probe call (`KIND_PROBE`) and the subsequent pay call (`KIND_PAY`) invoke `pool.addLiquidity(owner = allowlistedAddress, ...)`. The extension checks `owner` in both invocations and passes both times. The attacker (`msg.sender`) is the payer; the allowlisted address receives the shares.

---

### Impact Explanation

The `DepositAllowlistExtension` is the sole production mechanism for curated pools to restrict depositors. Because it checks the wrong actor (`owner` instead of `sender`), the restriction is entirely ineffective:

- Any non-allowlisted address can deposit into a curated pool by nominating any allowlisted address as `owner`.
- The attacker pays tokens and mutates pool state (bin balances, cursor position) without authorization.
- The allowlisted address receives unsolicited LP shares (grief); while they can remove them via `removeLiquidity`, the pool's bin cursor and balances have already been altered.
- An attacker can use this to move the pool cursor into a specific bin before a victim's trade, achieving price manipulation at the cost of the deposited tokens.

This breaks the core invariant that curated pools enforce: **only allowlisted addresses may interact with the deposit path**.

---

### Likelihood Explanation

Exploitation requires only a direct call to `pool.addLiquidity` (or a call through `MetricOmmPoolLiquidityAdder`) with any allowlisted address as `owner`. No privileged access, no special token, no oracle manipulation. Any on-chain observer can read the allowlist via `allowedDepositor` and pick a valid `owner`. The attacker must pay tokens but retains full control over the callback and the bin targeted.

---

### Recommendation

`DepositAllowlistExtension.beforeAddLiquidity` must check `sender` (the actual caller of `addLiquidity`) rather than `owner`:

```solidity
// current — wrong actor
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}

// fixed — gate the actual depositor
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

Note: when deposits are routed through `MetricOmmPoolLiquidityAdder`, `sender` will be the adder's address. Pool admins must either (a) allowlist the adder and rely on the adder's own `_validateOwner` / payer logic, or (b) require direct pool calls only. The interface and NatDoc for `beforeAddLiquidity` should be updated to clarify which actor the allowlist is intended to gate.

---

### Proof of Concept

```solidity
// Pool configured with DepositAllowlistExtension.
// Pool admin has allowlisted `alice` but NOT `attacker`.

// Attacker contract implements IMetricOmmModifyLiquidityCallback:
function metricOmmModifyLiquidityCallback(
    uint256 amount0Delta, uint256 amount1Delta, bytes calldata
) external {
    // pay tokens to pool
    IERC20(token0).transfer(msg.sender, amount0Delta);
    IERC20(token1).transfer(msg.sender, amount1Delta);
}

// Attacker calls:
pool.addLiquidity(
    alice,          // owner = allowlisted address → extension passes
    0,              // salt
    deltas,         // bins to deposit into
    "",             // callbackData
    ""              // extensionData
);
// Result: extension check passes (owner=alice is allowlisted),
//         LP shares minted to alice,
//         attacker pays tokens and mutates pool state.
// Allowlist is bypassed.
``` [1](#0-0) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L91-99)
```text
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L106-115)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, owner, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
    }
```
