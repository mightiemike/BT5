### Title
`DepositAllowlistExtension` Enforces Allowlist on LP Position `owner` Instead of Transaction `sender`, Allowing Any Address to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument and gates on `owner` (the LP position recipient) instead. Because `addLiquidity` lets the caller freely choose `owner`, any address — regardless of allowlist status — can deposit tokens into a restricted pool by naming an allowlisted address as the position owner. The configured guard is structurally bypassed on every call.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts two distinct address arguments: `msg.sender` (the actual token-providing caller, forwarded as `sender`) and the caller-supplied `owner` (the LP position recipient). Both are passed through `ExtensionCalling._beforeAddLiquidity` to the extension hook:

```solidity
// MetricOmmPool.sol
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

```solidity
// ExtensionCalling.sol
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but explicitly discards it (unnamed `address`), then checks only `owner`:

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

The contract's own naming (`allowedDepositor`, `setAllowedToDeposit`, `isAllowedToDeposit`) and NatSpec ("Gates `addLiquidity` by depositor address") confirm the intended subject is the token-providing caller, not the position recipient. The implementation checks the wrong address.

---

### Impact Explanation

Any unprivileged address can call `addLiquidity(owner = allowlisted_address, ...)`. The extension sees `owner` on the allowlist and permits the call. The actual token provider (`sender`) is never verified. Consequences:

1. **Allowlist fully bypassed**: A pool configured to accept deposits only from KYC'd, DAO-approved, or otherwise vetted parties accepts tokens from any address.
2. **Forced LP position griefing**: An attacker can mint LP shares into any allowlisted address without that address's consent. Because `removeLiquidity` enforces `msg.sender == owner`, the allowlisted address must act to unwind the position.
3. **Pool liquidity manipulation**: An attacker can alter the pool's bin balances and `curPosInBin` state — affecting oracle-anchored swap pricing — without satisfying the access control the pool admin deployed.

The analog to the external report is exact: a configured boundary (the deposit allowlist) is not applied to the correct variable (the actual depositor), so the guard is silently skipped for every non-allowlisted caller who supplies an allowlisted `owner`.

---

### Likelihood Explanation

- Requires no special privilege — any EOA or contract can call `addLiquidity`.
- The bypass is unconditional: it works on every pool that has `DepositAllowlistExtension` configured and does not set `allowAllDepositors = true`.
- The attacker only needs to know one allowlisted address (observable on-chain via `AllowedToDepositSet` events or `allowedDepositor` reads).

---

### Recommendation

Replace the unnamed first parameter with `sender` and enforce the allowlist on the actual token provider:

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

1. Pool `P` is deployed with `DepositAllowlistExtension`. Admin calls `setAllowedToDeposit(P, alice, true)`. Bob is not allowlisted.
2. Bob calls `P.addLiquidity(owner = alice, salt, deltas, callbackData, extensionData)`.
3. Pool calls `extension.beforeAddLiquidity(sender=Bob, owner=alice, ...)`.
4. Extension checks `allowedDepositor[P][alice] == true` → no revert.
5. Bob's tokens are pulled via the liquidity callback; an LP position is minted for `alice`.
6. Bob has deposited into a restricted pool without being on the allowlist. Alice holds an LP position she never requested. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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
