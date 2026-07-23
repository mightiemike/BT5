### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual caller of `addLiquidity`) and instead gates access on `owner` (a freely caller-supplied position-owner address). Because `owner` is an arbitrary input, any unprivileged address can bypass the allowlist by naming an already-allowlisted address as `owner`.

---

### Finding Description

The pool calls extensions with two distinct identity parameters:

- `sender` — `msg.sender` inside `MetricOmmPool.addLiquidity`, i.e. the address that actually initiates the deposit and provides tokens via callback.
- `owner` — a caller-supplied argument that designates who will own the resulting LP position.

`ExtensionCalling._beforeAddLiquidity` encodes both and forwards them to the extension:

```solidity
// ExtensionCalling.sol L97
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` receives `(address sender, address owner, …)` but discards `sender` (the first `address` is unnamed) and checks only `owner`:

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

`msg.sender` here is the pool (correct for the pool-key lookup), but `owner` is attacker-controlled. The admin populates the allowlist via:

```solidity
// DepositAllowlistExtension.sol L18-20
function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
```

So `allowedDepositor[pool][owner]` is the check, and `owner` is freely chosen by the caller.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly uses the `sender` parameter (the actual swap initiator):

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

The inconsistency is the root cause: `DepositAllowlistExtension` checks the wrong identity.

---

### Impact Explanation

Any address — regardless of allowlist status — can call `MetricOmmPool.addLiquidity` with `owner` set to any address that is already on the allowlist. The extension check passes, the deposit proceeds, and the position is minted to `owner`. The deposit allowlist is rendered completely ineffective. Pools configured to restrict liquidity provision to a curated set of depositors (e.g., private/institutional pools, regulatory-compliant pools) have that restriction fully bypassed by any unprivileged actor.

Secondary effect: the allowlisted `owner` receives an LP position they did not request. Because `removeLiquidity` enforces `msg.sender == owner`, the attacker cannot reclaim the deposited tokens — but the forced position assignment is a griefing vector against the `owner`.

---

### Likelihood Explanation

Exploitation requires only knowledge of one allowlisted address (trivially discoverable on-chain from `AllowedToDepositSet` events or direct mapping reads) and the ability to call `addLiquidity` with a crafted `owner` argument. No special privileges, flash loans, or complex setup are needed. Any pool that deploys `DepositAllowlistExtension` with a non-empty allowlist is immediately vulnerable.

---

### Recommendation

Replace the unnamed first parameter with `sender` and gate on it, mirroring `SwapAllowlistExtension`:

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

This aligns with the interface intent (`sender` = the depositing actor) and with the parallel `SwapAllowlistExtension` implementation.

---

### Proof of Concept

1. Pool `P` is deployed with `DepositAllowlistExtension` configured; `allowedDepositor[P][alice] = true`, `allowedDepositor[P][attacker] = false`.
2. Attacker calls `P.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
3. Pool calls `extension.beforeAddLiquidity(attacker, alice, salt, deltas, extensionData)`.
4. Extension checks `allowedDepositor[P][alice]` → `true` → no revert.
5. Attacker's callback provides tokens; position is minted to `alice`.
6. Attacker has deposited into a restricted pool despite not being allowlisted. Alice holds an unrequested position.

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
