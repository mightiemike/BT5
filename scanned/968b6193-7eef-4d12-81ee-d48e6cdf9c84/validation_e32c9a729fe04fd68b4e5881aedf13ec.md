### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument (the actual caller of `addLiquidity`) and validates `owner` instead — a fully caller-controlled parameter. Because `owner` is supplied by the caller, any unprivileged address can bypass the allowlist by naming any already-allowlisted address as the position owner.

---

### Finding Description

`ExtensionCalling._beforeAddLiquidity` encodes the hook call as:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeAddLiquidity,
    (sender, owner, salt, deltas, extensionData)
)
```

`sender` is `msg.sender` of the pool's `addLiquidity` call — the actual depositor. `owner` is the position-owner address supplied by that same caller.

`DepositAllowlistExtension.beforeAddLiquidity` receives both but discards `sender`:

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

The first `address` parameter — `sender` — is unnamed and never read. The guard checks `allowedDepositor[pool][owner]`, not `allowedDepositor[pool][sender]`.

**Bypass path:**

1. Pool is deployed with `DepositAllowlistExtension`; pool admin allowlists `alice`.
2. Unauthorized `attacker` calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
3. Pool calls `_beforeAddLiquidity(attacker /*sender*/, alice /*owner*/, ...)`.
4. Extension checks `allowedDepositor[pool][alice]` → `true` → passes.
5. `LiquidityLib.addLiquidity` executes; the swap-callback fires on `attacker` (the actual payer), tokens are pulled from `attacker`, and a position is minted under `alice`.
6. The allowlist was never consulted for `attacker`.

The `removeLiquidity` path enforces `msg.sender == owner`, so `attacker` cannot reclaim the deposited tokens — but the allowlist guard is still completely defeated: any address can inject liquidity into a pool the admin intended to restrict.

---

### Impact Explanation

The `DepositAllowlistExtension` is the sole mechanism for restricting who may provide liquidity to a pool. With the guard checking the wrong address, the restriction is entirely ineffective. Any unprivileged address can:

- Add liquidity to bins the admin did not intend to open to them, altering pool depth and price distribution.
- Circumvent KYC/compliance controls the pool admin configured.
- Force the pool into states (bin balances, `curPosInBin`, `binTotals`) that downstream extensions (e.g., `OracleValueStopLossExtension`) or integrators did not anticipate.

This is an admin-boundary break: a pool-admin-configured guard is bypassed by an unprivileged path, matching the allowed impact gate.

---

### Likelihood Explanation

The bypass requires no special conditions, no flash loan, and no privileged access. Any EOA or contract can trigger it in a single transaction by supplying any allowlisted address as `owner`. The allowlisted address need not cooperate. Likelihood is **high**.

---

### Recommendation

Name and validate `sender` instead of `owner`:

```diff
- function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+ function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
      external view override returns (bytes4)
  {
-     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
          revert IMetricOmmPoolActions.NotAllowedToDeposit();
      }
      return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

---

### Proof of Concept

```
Setup
─────
pool  = MetricOmmPool (DepositAllowlistExtension configured)
alice = allowlisted address (allowedDepositor[pool][alice] = true)
eve   = NOT allowlisted

Attack
──────
eve calls:
  pool.addLiquidity(
      alice,          // owner  ← eve controls this
      0,              // salt
      deltas,         // any valid bin deltas
      "",             // callbackData
      ""              // extensionData
  )

Extension hook fires:
  beforeAddLiquidity(eve /*sender — ignored*/, alice /*owner — checked*/)
  → allowedDepositor[pool][alice] == true → no revert

LiquidityLib.addLiquidity executes:
  → callback fires on eve, pulling tokens from eve
  → position minted: owner=alice, salt=0

Result:
  eve successfully deposited into a restricted pool.
  alice now holds a position she did not request.
  The allowlist provided zero protection.
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
