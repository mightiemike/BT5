### Title
`DepositAllowlistExtension` gates LP-share recipient (`owner`) instead of actual depositor (`sender`), allowing any unprivileged caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual depositor, i.e. `msg.sender` of `pool.addLiquidity`) and instead checks the caller-supplied `owner` argument (the LP-share recipient). Because `owner` is freely chosen by the caller, any unprivileged address can bypass the deposit allowlist by naming an already-allowlisted address as `owner`, paying the required tokens via the callback, and depositing liquidity into a pool that was supposed to be restricted.

---

### Finding Description

**Root cause — wrong identity variable checked**

In `DepositAllowlistExtension.beforeAddLiquidity` the first parameter (`sender`) is explicitly discarded and the allowlist lookup is performed on `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`owner` is the address that will receive the minted LP shares. It is a plain calldata argument to `pool.addLiquidity` and is therefore fully controlled by the caller:

```solidity
// metric-core/contracts/MetricOmmPool.sol
function addLiquidity(
    address owner,          // ← caller-supplied, not authenticated
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) {
    ...
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
        _liquidityContext(), owner, salt, deltas, callbackData, ...
    );
    ...
}
```

`_beforeAddLiquidity` forwards `msg.sender` as `sender` and the caller-chosen `owner` as `owner`:

```solidity
// metric-core/contracts/ExtensionCalling.sol
function _beforeAddLiquidity(address sender, address owner, ...) internal {
    _callExtensionsInOrder(
        BEFORE_ADD_LIQUIDITY_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
}
```

The extension receives both values but throws away `sender` and only checks `owner`. Because `owner` is attacker-controlled, the guard is trivially bypassed.

**Analogy to the seed bug**

The external Merkle-proof bug loops over `branch.length` (user-supplied) instead of the bits of `index` (the authoritative value). Here, the allowlist check is performed on `owner` (user-supplied) instead of `sender` (the authoritative depositor identity). In both cases, a user-controlled input replaces the authoritative control variable, making the guard meaningless.

---

### Impact Explanation

Any unprivileged address can add liquidity to a pool that is supposed to be restricted to allowlisted depositors. The attacker pays the required tokens (via the pool's liquidity callback) and the allowlisted address receives the LP shares. Consequences:

- The deposit allowlist — the pool admin's primary access-control mechanism for liquidity — is completely nullified.
- Unauthorized parties can alter the pool's bin-level liquidity composition, affecting price discovery and LP returns for existing position holders.
- If the pool is used as a permissioned venue (e.g., institutional-only, KYC-gated), the invariant that only vetted LPs contribute liquidity is broken, which can have regulatory and financial consequences for the pool operator and existing LPs.

This matches the allowed impact gate: **admin-boundary break — factory/pool role checks bypassed by an unprivileged path**.

---

### Likelihood Explanation

- Requires a pool with `DepositAllowlistExtension` configured and at least one allowlisted address (common in permissioned deployments).
- The bypass is a single direct call to `pool.addLiquidity` with a known allowlisted address as `owner`; no special privileges, flash loans, or multi-step setup are needed.
- The attacker must supply the tokens, but the economic cost is bounded by the deposit size they choose; they can deposit a dust amount to prove the bypass or a large amount to manipulate pool state.

---

### Recommendation

Check `sender` (the actual depositor, `msg.sender` of `pool.addLiquidity`) instead of — or in addition to — `owner`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    // Gate the actual depositor, not the LP-share recipient.
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate both who pays and who receives shares, check both `sender` and `owner`.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` as a `beforeAddLiquidity` hook.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is authorized.
3. Unauthorized `charlie` calls:
   ```solidity
   pool.addLiquidity(
       alice,          // owner — allowlisted, passes the check
       0,              // salt
       deltas,         // any valid liquidity delta
       callbackData,   // charlie's callback pays the tokens
       extensionData
   );
   ```
4. `beforeAddLiquidity` receives `sender = charlie`, `owner = alice`. It discards `charlie` and checks `allowedDepositor[pool][alice]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` mints LP shares to `alice`; `charlie`'s callback pays the tokens.
6. `charlie` has successfully deposited into a restricted pool. The deposit allowlist is bypassed. [1](#0-0) [2](#0-1) [3](#0-2)

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
