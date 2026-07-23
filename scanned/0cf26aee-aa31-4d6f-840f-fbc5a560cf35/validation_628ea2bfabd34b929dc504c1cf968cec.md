### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the `owner` parameter (the LP position recipient) rather than the `sender` parameter (the actual caller of `addLiquidity`). Because `owner` is a free caller-supplied argument with no `msg.sender == owner` enforcement in the pool, any unprivileged address can bypass the allowlist entirely by naming an already-allowed address as `owner`.

---

### Finding Description

`addLiquidity` passes two distinct addresses into the extension hook: [1](#0-0) 

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
//                  ^^^^^^^^^^  ^^^^^
//                  sender      owner (caller-supplied, no equality check)
```

Inside the extension, the first parameter (`sender`) is silently discarded and only `owner` is tested against the allowlist: [2](#0-1) 

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

The pool never enforces `msg.sender == owner` before calling the hook: [3](#0-2) 

So any address can call `pool.addLiquidity(owner = allowedAddress, ...)`, the guard reads `allowedDepositor[pool][allowedAddress]` → `true`, and the deposit proceeds. The caller pays the tokens via the swap callback; the LP position is minted to `allowedAddress`.

This is structurally identical to the RocketPool analog: a guard flag is evaluated against the wrong variable, so the check passes even though the acting party is unauthorized.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly gates on `sender` (the actual caller): [4](#0-3) 

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

The design inconsistency confirms the `DepositAllowlistExtension` check is misbound.

Additionally, `removeLiquidity` enforces `msg.sender == owner`: [5](#0-4) 

This means the unauthorized depositor cannot reclaim the tokens they paid — the funds are permanently locked in a position owned by `allowedAddress`.

---

### Impact Explanation

- **Admin-boundary break**: The pool admin's deposit allowlist — a core access-control configuration — is fully bypassed by any unprivileged caller. The invariant "only allowlisted addresses may add liquidity" is violated.
- **LP dilution**: An attacker can inject liquidity into specific bins, diluting the fee share of existing LPs in those bins without their consent.
- **Permanent token loss for the caller**: Because `removeLiquidity` requires `msg.sender == owner`, the unauthorized depositor cannot recover the tokens they paid. This creates an irreversible fund-impacting outcome for the caller.
- **Unauthorized pool state manipulation**: Forced liquidity additions can shift `curBinIdx`/`curPosInBin` accounting and interact adversely with the `OracleValueStopLossExtension` watermark logic, which reads live bin balances after each swap.

---

### Likelihood Explanation

Exploitation requires only knowing one address that is on the allowlist (trivially observable on-chain from past `AllowedToDepositSet` events) and calling `addLiquidity` with that address as `owner`. No special role, flash loan, or oracle manipulation is needed. Any EOA or contract can trigger this in a single transaction.

---

### Recommendation

Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

```solidity
// current (wrong):
function beforeAddLiquidity(address, address owner, ...) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) { ... }

// fixed:
function beforeAddLiquidity(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) { ... }
``` [2](#0-1) 

If the intended semantic is "only allowed addresses may *own* LP positions" (rather than "only allowed addresses may *call* addLiquidity"), then both `sender` and `owner` should be checked, and the admin-facing setter/getter documentation must be updated to reflect the dual requirement.

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  bob is NOT on the allowlist

Attack:
  bob calls pool.addLiquidity(
      owner    = alice,   // alice is allowed → check passes
      salt     = 0,
      deltas   = { binIdxs: [0], shares: [1_000_000] },
      callbackData = "",
      extensionData = ""
  )

Extension check:
  allowedDepositor[pool][alice] == true  →  no revert

Result:
  - bob's tokens are pulled via metricOmmModifyLiquidityCallback
  - LP position minted to alice (bob cannot remove it: msg.sender != owner)
  - bob's tokens are permanently lost; pool receives unauthorized liquidity
  - deposit allowlist is fully bypassed
```

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

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
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
