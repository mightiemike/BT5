### Title
`DepositAllowlistExtension` checks position `owner` instead of actual caller `sender`, allowing any address to bypass the deposit allowlist guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the position `owner` parameter against the per-pool allowlist but silently ignores the `sender` argument (the actual `msg.sender` who pays tokens). Because `MetricOmmPool.addLiquidity` explicitly supports an operator pattern where the payer (`msg.sender`) and the position owner (`owner`) are different addresses, any unprivileged caller can bypass the deposit allowlist entirely by naming any already-allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`msg.sender` is the payer (the entity that will be called back to supply tokens); `owner` is the address whose position ledger is credited. The NatSpec explicitly documents this split: *"`msg.sender` pays but need not equal `owner` (operator pattern)."* [1](#0-0) 

The pool then encodes both into the extension call: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but discards it (unnamed `address`). It only checks `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

The extension's own NatSpec says it *"Gates `addLiquidity` by depositor address"* and `setAllowedToDeposit` names its second parameter `depositor` — both signal the intent is to gate the paying actor, not the position owner. [4](#0-3) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swapper), not the `recipient`: [5](#0-4) 

The inconsistency is the root cause: the deposit guard checks the wrong address.

---

### Impact Explanation

**Severity: Medium**

The deposit allowlist guard is completely nullified. A pool admin who configures `DepositAllowlistExtension` to restrict liquidity provision to a curated set of addresses (e.g., KYC-gated LPs, institutional partners, or a whitelist-only launch) receives no protection. Any unprivileged address can call `pool.addLiquidity(allowlistedAddress, salt, deltas, ...)` directly, pass the allowlist check (because `allowlistedAddress` is allowlisted), pay the tokens via the modify-liquidity callback, and credit the position to `allowlistedAddress`. The pool's configured access-control invariant — that only allowlisted depositors may add liquidity — is broken by a single direct pool call with no special privileges.

---

### Likelihood Explanation

**Likelihood: Medium**

Allowlisted addresses are discoverable on-chain via `AllowedToDepositSet` events emitted by `setAllowedToDeposit`. Any actor who reads those events can immediately construct the bypass call. No flash loan, price manipulation, or privileged role is required — only a direct call to `pool.addLiquidity` with a known allowlisted address as `owner`. [6](#0-5) 

---

### Recommendation

Replace the unnamed first parameter with `sender` and check it instead of (or in addition to) `owner`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender]
        && !allowedDepositor[msg.sender][sender])   // ← check the payer, not the owner
    {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intended semantic is "only allowlisted addresses may hold positions", check `owner`. If it is "only allowlisted addresses may pay to deposit", check `sender`. If both must be gated, check both. The current code does neither correctly for the payer path.

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  admin calls: depositExtension.setAllowedToDeposit(pool, ALICE, true)
  BOB is NOT on the allowlist

Attack:
  BOB calls pool.addLiquidity(
      owner        = ALICE,   // allowlisted → check passes
      salt         = 1,
      deltas       = <valid bins/shares>,
      callbackData = <BOB pays tokens via metricOmmModifyLiquidityCallback>,
      extensionData = ""
  )

Result:
  - DepositAllowlistExtension checks allowedDepositor[pool][ALICE] → true → no revert
  - BOB's callback pays the required tokens
  - Position shares are credited to ALICE at (ALICE, salt=1)
  - BOB has successfully deposited into a deposit-gated pool without being allowlisted
  - The pool admin's access-control invariant is violated
``` [7](#0-6) [3](#0-2)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-98)
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
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-20)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

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
