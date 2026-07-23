### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is designed to gate `addLiquidity` by depositor address. However, its `beforeAddLiquidity` hook silently drops the `sender` argument and checks only `owner` (the position holder). Because `MetricOmmPool.addLiquidity` lets any `msg.sender` supply an arbitrary `owner`, any non-allowlisted address can bypass the guard by naming an allowlisted address as `owner` while itself acting as the actual token provider.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` parameter as `owner` to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`DepositAllowlistExtension.beforeAddLiquidity` receives both values but discards `sender` (unnamed first argument) and checks only `owner`:

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

`msg.sender` inside the extension is the pool (correct for the pool-identity check), and `owner` is the position recipient. The actual depositor — the address that will be called back to transfer tokens — is `sender`, which is never read.

There is no restriction in `addLiquidity` on who may call it or what `owner` they may name:

```solidity
// MetricOmmPool.sol lines 182-196
function addLiquidity(
    address owner,          // ← caller-controlled
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) ...
```

---

### Impact Explanation

The deposit allowlist is an admin-configured boundary intended to restrict which addresses may add liquidity (e.g., for KYC/compliance, whitelist-only pools, or controlled liquidity bootstrapping). Because the guard checks the position recipient (`owner`) rather than the actual token provider (`sender`), any non-allowlisted address can:

1. Call `pool.addLiquidity(allowlisted_address, salt, deltas, callbackData, extensionData)`.
2. Pass the extension check (the allowlisted address is approved).
3. Provide tokens via the `metricOmmAddLiquidityCallback` on itself.
4. Inject liquidity into the pool in violation of the admin's access policy.

In a collusion scenario (attacker controls or coordinates with an allowlisted address), the attacker recovers their tokens after the allowlisted address removes liquidity, achieving a full allowlist bypass with no net token loss. Even without collusion, the attacker's tokens enter the pool and the allowlisted address receives an unsolicited position — a griefing vector that forces the victim to spend gas to remove it. The pool admin's core invariant ("only approved addresses may deposit") is broken by an unprivileged path.

---

### Likelihood Explanation

The bypass requires only a single `addLiquidity` call with a known allowlisted address as `owner`. No special permissions, flash loans, or oracle manipulation are needed. Any allowlisted address is publicly discoverable from `AllowedToDepositSet` events. Likelihood is high.

---

### Recommendation

Check `sender` (the actual token provider) instead of `owner` (the position recipient):

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

If the intent is to restrict both who may deposit and who may receive a position, check both `sender` and `owner`.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` configured on `beforeAddLiquidity`.
2. Admin calls `setAllowedToDeposit(pool, alice, true)` — Alice is allowlisted; Bob is not.
3. Bob (non-allowlisted) constructs a contract implementing `IMetricOmmAddLiquidityCallback`.
4. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
5. Pool calls `extension.beforeAddLiquidity(bob, alice, ...)` — extension checks `allowedDepositor[pool][alice]` → `true` → no revert.
6. Pool calls `LiquidityLib.addLiquidity(... alice ...)` — Alice receives the position.
7. Pool calls `bob.metricOmmAddLiquidityCallback(...)` — Bob transfers tokens.
8. Bob's tokens are now in the pool; the allowlist guard was never triggered against Bob.
9. (Optional collusion) Alice calls `removeLiquidity` and returns tokens to Bob — full bypass with no net loss.

**Corrupted invariant**: `allowedDepositor[pool][sender]` is never evaluated; the guard is bound to the wrong identity, making the allowlist ineffective against any address that can name an approved `owner`. [1](#0-0) [2](#0-1) [3](#0-2)

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
