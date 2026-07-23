### Title
`DepositAllowlistExtension` Guards LP Owner Instead of the Actual Depositor, Allowing Any Unprivileged Address to Bypass the Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` checks the `owner` parameter (the LP-position recipient) rather than the `sender` parameter (the actual caller of `addLiquidity`). Because `owner` is a free caller-supplied argument, any address can bypass the allowlist by naming an already-authorized address as `owner`. The guard is structurally analogous to the flash-loan bug: the wrong variable is compared, so the invariant the guard is meant to enforce is never actually enforced against the real actor.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses to the extension hook:

- `sender` = `msg.sender` (the address that called `addLiquidity` and will pay tokens via the swap callback)
- `owner` = a caller-supplied argument that receives the LP shares [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently discards `sender` (first parameter is unnamed) and only checks `owner`: [3](#0-2) 

Contrast this with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual caller): [4](#0-3) 

The inconsistency is the root cause. Because `owner` is a free argument, any address can pass the allowlist check by supplying an authorized address as `owner`.

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may add liquidity (e.g., for regulatory compliance, curated LP sets, or controlled pool bootstrapping). With this bug:

1. Any unprivileged address can call `addLiquidity` on a restricted pool.
2. The allowlist check passes because the attacker names an authorized address as `owner`.
3. Tokens are pulled from the attacker via the swap callback; LP shares are minted to `owner`.
4. `removeLiquidity` enforces `msg.sender == owner`, so the attacker cannot reclaim the shares — but the allowlist invariant is permanently broken: unauthorized capital enters the pool.

The pool admin's access-control boundary is bypassed by an unprivileged path, matching the "Admin-boundary break" impact criterion. The pool receives liquidity from sources the admin explicitly excluded, which can distort bin balances, affect oracle-anchored swap math, and undermine any compliance or economic rationale behind the allowlist.

---

### Likelihood Explanation

Exploitation requires only a standard `addLiquidity` call with a known authorized address as `owner`. No special privileges, flash loans, or oracle manipulation are needed. Any address that can observe the allowlist state (public mappings) can exploit this immediately. [5](#0-4) 

---

### Recommendation

Check `sender` (the actual depositor) instead of `owner` (the LP recipient), mirroring the pattern used in `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate both the depositor and the owner, both should be checked independently.

---

### Proof of Concept

```
Setup:
  pool P has DepositAllowlistExtension configured
  allowedDepositor[P][AUTHORIZED] = true
  allowedDepositor[P][ATTACKER]   = false (not set)

Attack:
  ATTACKER calls pool.addLiquidity(
      owner    = AUTHORIZED,   // passes the allowlist check
      salt     = 0,
      deltas   = <valid delta>,
      callbackData = ...,
      extensionData = ""
  )

Extension check (beforeAddLiquidity):
  msg.sender = P (the pool)
  owner      = AUTHORIZED
  allowedDepositor[P][AUTHORIZED] == true  → check passes

Result:
  - ATTACKER's tokens are pulled via metricOmmSwapCallback
  - LP shares are minted to AUTHORIZED
  - ATTACKER has deposited into a pool that explicitly excluded them
  - The allowlist invariant is violated
``` [3](#0-2) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-14)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
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
