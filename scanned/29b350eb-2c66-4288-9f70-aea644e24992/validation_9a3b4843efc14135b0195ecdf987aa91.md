### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Unauthorized Depositor to Bypass the Allowlist Guard — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is designed to gate `addLiquidity` by depositor address. Its `beforeAddLiquidity` hook silently drops the `sender` parameter and checks `owner` (the LP-position recipient) instead. Because `owner` is caller-supplied and can be set to any address already on the allowlist, any unauthorized party can bypass the guard entirely.

---

### Finding Description

In `DepositAllowlistExtension.beforeAddLiquidity` the first positional argument — `sender`, the actual `msg.sender` of the `pool.addLiquidity()` call — is unnamed and ignored. The allowlist check is performed against `owner`, the LP-position recipient address: [1](#0-0) 

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

The pool passes both values correctly. In `ExtensionCalling._beforeAddLiquidity`, `sender` is `msg.sender` of the pool call (the actual depositor) and `owner` is the LP-position owner address supplied by the caller: [2](#0-1) 

And in `MetricOmmPool.addLiquidity`, `msg.sender` (the real depositor) is forwarded as `sender`, while `owner` is a free parameter the caller controls: [3](#0-2) 

The naming of the admin setter and view function makes the intended semantics unambiguous — the guard is supposed to check the *depositor*, not the position owner: [4](#0-3) 

The sibling `SwapAllowlistExtension.beforeSwap` correctly names and checks `sender` (the actual swapper), confirming the asymmetry is a defect, not a design choice: [5](#0-4) 

---

### Impact Explanation

An unauthorized party (not present in `allowedDepositor[pool]`) can bypass the allowlist by calling:

```
pool.addLiquidity(allowedAddress, salt, deltas, callbackData, extensionData)
```

The extension evaluates `allowedDepositor[pool][allowedAddress]` — which is `true` — and the hook returns success. The pool then executes `LiquidityLib.addLiquidity` with `owner = allowedAddress`. The unauthorized caller funds the deposit via the swap callback; the LP position is credited to `allowedAddress`.

Consequences:
- The pool's deposit access-control invariant is fully broken: any address can add liquidity to a restricted pool.
- The unauthorized depositor permanently loses the deposited tokens (they cannot call `removeLiquidity` because `msg.sender != owner` is enforced there).
- The `allowedAddress` receives an unsolicited LP position, which it can later redeem — effectively a forced token transfer into the pool on behalf of an arbitrary third party.
- If the pool also runs `OracleValueStopLossExtension`, the injected liquidity alters per-bin token balances and therefore the per-share metrics used to update high-watermarks after the next swap, potentially manipulating stop-loss thresholds for all LPs in those bins. [6](#0-5) 

---

### Likelihood Explanation

High. The bypass requires no special privilege, no flash loan, and no complex setup. Any EOA or contract can call `pool.addLiquidity` with `owner` set to any address already on the allowlist. The allowlist is publicly readable, so the attacker can trivially identify a valid `owner` value.

---

### Recommendation

Rename the first parameter and check `sender` instead of `owner`:

```solidity
// Before (buggy)
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// After (fixed)
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
```

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`.

---

### Proof of Concept

1. Factory deploys a pool with `DepositAllowlistExtension` attached to the `beforeAddLiquidity` hook.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is **not** on the allowlist.
3. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. Pool calls `_beforeAddLiquidity(sender=Bob, owner=Alice, ...)`.
5. Extension evaluates `allowedDepositor[pool][Alice]` → `true` → hook returns success.
6. Pool executes `LiquidityLib.addLiquidity(..., owner=Alice, ...)`.
7. Bob's callback transfers tokens into the pool; Alice's LP shares are minted.
8. Bob has deposited into a pool he was explicitly barred from; Alice holds an LP position she never requested.
9. Bob cannot recover his tokens: `removeLiquidity` enforces `msg.sender == owner`, so only Alice can withdraw. [1](#0-0) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-30)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }

  function setAllowAllDepositors(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllDepositors[pool_] = allowed;
    emit AllowAllDepositorsSet(pool_, allowed);
  }

  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
  }
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
