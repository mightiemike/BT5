### Title
`DepositAllowlistExtension.beforeAddLiquidity` validates LP position `owner` instead of the actual depositor `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook silently drops the `sender` argument and instead validates the `owner` (LP position recipient). Because `owner` is a free caller-supplied parameter in `MetricOmmPool.addLiquidity`, any address not on the allowlist can deposit into a restricted pool by nominating an authorized address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` forwards two distinct actors to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

- `msg.sender` — the **actual depositor** who will be called back to supply tokens.
- `owner` — a **free parameter** naming who receives the LP shares; it can be any address.

`DepositAllowlistExtension.beforeAddLiquidity` ignores `sender` entirely and checks only `owner`:

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

The parallel `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swap initiator):

```solidity
// SwapAllowlistExtension.sol lines 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The asymmetry is the root cause: the swap guard checks the right actor; the deposit guard checks the wrong one.

---

### Impact Explanation

An unprivileged caller (not on the allowlist) can:

1. Call `pool.addLiquidity(owner = <any allowlisted address>, ...)`.
2. The extension evaluates `allowedDepositor[pool][allowlistedAddress]` → `true` → hook passes.
3. The pool issues a `metricOmmSwapCallback` to `msg.sender` (the unauthorized caller) to collect tokens.
4. LP shares are minted to the allowlisted `owner`.

The pool admin's deposit restriction — the sole purpose of `DepositAllowlistExtension` — is completely nullified. Any actor can inject liquidity into a pool that is supposed to be closed to them, constituting an admin-boundary break: a pool-admin-configured access control is bypassed by an unprivileged path.

Secondary consequences include:
- Unauthorized actors can shift bin balances and `curPosInBin`, altering the marginal price seen by subsequent swaps and harming existing LPs.
- If `OracleValueStopLossExtension` is co-configured, a crafted deposit can move bin metrics to trigger a stop-loss revert on the next swap, making the pool temporarily unusable for legitimate LPs.

---

### Likelihood Explanation

Exploitation requires only:
- Knowing one allowlisted address (observable on-chain via `AllowedToDepositSet` events or direct mapping reads).
- Holding enough token0/token1 to satisfy the callback.

No privileged role, flash loan, or special setup is needed. Any EOA or contract can execute this in a single transaction.

---

### Recommendation

Replace the ignored first parameter with `sender` and validate it, mirroring `SwapAllowlistExtension`:

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

Also update `isAllowedToDeposit` and the admin setter NatDoc to clarify that the controlled address is the **caller of `addLiquidity`**, not the LP position owner.

---

### Proof of Concept

```
Setup
─────
pool P configured with DepositAllowlistExtension E
allowedDepositor[P][Alice] = true
allowedDepositor[P][Bob]   = false   // Bob is blocked

Attack
──────
Bob calls:
  P.addLiquidity(
      owner    = Alice,   // allowlisted — passes the guard
      salt     = 0,
      deltas   = <large position>,
      callbackData = ...,
      extensionData = ""
  )

Extension hook receives:
  beforeAddLiquidity(sender=Bob, owner=Alice, ...)
  → checks allowedDepositor[P][Alice] == true  ✓
  → hook returns selector, no revert

Pool issues metricOmmSwapCallback to Bob (msg.sender).
Bob transfers tokens; Alice receives LP shares.

Result: Bob deposited into a pool he is explicitly barred from.
        The allowlist invariant is broken.
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
