### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Complete Deposit Allowlist Bypass — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook silently discards the `sender` argument and checks only `owner`. Because `owner` is a free caller-supplied parameter in `MetricOmmPool.addLiquidity`, any unprivileged address can bypass the allowlist by setting `owner` to any already-allowlisted address.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-controlled `owner` parameter and passes both `msg.sender` (the actual depositor) and `owner` (the intended LP-position holder) to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both values faithfully: [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first positional argument (`sender`) is **unnamed and ignored**. The allowlist lookup is performed only against `owner`: [3](#0-2) 

The contract's own NatSpec states the intent is to gate by **depositor** address: [4](#0-3) 

Because `addLiquidity` imposes no constraint that `msg.sender == owner`, any address can call `addLiquidity(owner = allowlisted_address, ...)`. The pool invokes the extension with `sender = attacker, owner = allowlisted_address`. The extension evaluates `allowedDepositor[pool][allowlisted_address]` → `true` and permits the deposit. The attacker's tokens are pulled via the swap callback; the allowlisted address receives the LP shares.

---

### Impact Explanation

The deposit allowlist is completely defeated. Any address — regardless of allowlist status — can add liquidity to a pool that is supposed to be restricted, simply by naming an allowlisted address as `owner`. This breaks the core invariant the extension exists to enforce. Downstream consequences include:

- Unauthorized actors gaining LP exposure in pools intended to be private or regulatory-compliant.
- Allowlisted addresses receiving unsolicited LP positions (which they cannot refuse, since `removeLiquidity` requires `msg.sender == owner`), potentially exposing them to pool losses they did not consent to.
- The stop-loss watermark state (`OracleValueStopLossExtension`) being influenced by attacker-injected liquidity, since watermarks are computed per-bin over all shares.

---

### Likelihood Explanation

The bypass requires no privileges, no special tokens, and no complex setup. Any EOA or contract can execute it in a single transaction. The only cost to the attacker is the tokens deposited (which go to the allowlisted owner's LP position, not to the attacker).

---

### Recommendation

Check `sender` (the actual depositor) rather than `owner`:

```solidity
function beforeAddLiquidity(
    address sender,   // ← use this, not owner
    address,
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

If the intended semantics are "only allowlisted addresses may own positions", both `sender` and `owner` should be checked.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension`; pool admin allowlists `Alice` via `setAllowedToDeposit(pool, Alice, true)`.
2. `Bob` (not allowlisted) calls `pool.addLiquidity(owner = Alice, salt, deltas, callbackData, extensionData)`.
3. Pool calls `extension.beforeAddLiquidity(sender = Bob, owner = Alice, ...)`.
4. Extension evaluates `allowedDepositor[pool][Alice]` → `true`; no revert.
5. `LiquidityLib.addLiquidity` executes: Bob's tokens are pulled via callback, Alice receives LP shares.
6. Bob has successfully deposited into a pool he is not authorized to access. The allowlist is bypassed. [3](#0-2) [1](#0-0)

### Citations

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-13)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
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
