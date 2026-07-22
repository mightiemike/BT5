### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Non-Allowlisted Depositors to Bypass the Guard — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the `owner` (position recipient) against the per-pool allowlist, but ignores the `sender` (the actual caller of `addLiquidity` who pays the tokens). Because `addLiquidity` accepts any arbitrary `owner` address with no restriction on who the caller is, any unprivileged address can bypass the allowlist entirely by depositing into an allowlisted user's position. This is the direct Metric OMM analog of the `unstakeDelay` bug: a guard meant to restrict a class of actors is defeated because the wrong actor identity is checked.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` parameter and passes both `msg.sender` (as `sender`) and `owner` to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

The extension hook receives both identities:

```solidity
// ExtensionCalling.sol line 88-99
function _beforeAddLiquidity(address sender, address owner, ...) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
}
```

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but silently discards it, checking only `owner`:

```solidity
// DepositAllowlistExtension.sol line 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`msg.sender` inside the extension is the pool address (the caller of the extension), so `allowedDepositor[pool][owner]` is the check performed. The actual depositor (`sender`) is never validated.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual caller of `swap`):

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The inconsistency is the root cause: the deposit guard checks the wrong identity.

---

### Impact Explanation

Any address — regardless of allowlist status — can call `addLiquidity(alice, salt, deltas, ...)` where `alice` is an allowlisted address. The allowlist check passes because `alice` is approved. The caller (`msg.sender`) pays the tokens via the swap callback and the liquidity is credited to `alice`'s position. The pool admin's access control is fully defeated: non-allowlisted principals can deposit tokens into the pool at will. For pools configured as permissioned venues (institutional, KYC-gated, or regulatory-compliance pools), this breaks the core invariant that only approved depositors interact with pool liquidity. Additionally, the allowlisted victim (`alice`) receives an unwanted LP position and bears LP risk (price exposure, impermanent loss) until she removes it, incurring gas costs she did not consent to.

Severity: **Medium** — broken admin-configured access control with no direct fund theft but with forced LP exposure on victims and full allowlist bypass for the attacker.

---

### Likelihood Explanation

Exploitation requires no special privileges, no flash loan, and no oracle manipulation. Any EOA or contract can call `addLiquidity` with an arbitrary `owner`. The only cost is the token amount deposited (which the attacker controls and can minimize). The attack is trivially repeatable.

---

### Recommendation

Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

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

If the intent is to gate by position recipient (owner), document this explicitly and add a separate `sender` check so that neither the payer nor the recipient can be non-allowlisted.

---

### Proof of Concept

**Setup:**
- Pool `P` has `DepositAllowlistExtension` configured with `allowAllDepositors[P] = false`.
- `allowedDepositor[P][alice] = true`; Mallory is not on the allowlist.

**Attack:**
1. Mallory calls `P.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
2. Pool calls `DepositAllowlistExtension.beforeAddLiquidity(mallory, alice, ...)`.
3. Extension checks `allowedDepositor[P][alice]` → `true` → no revert.
4. Pool calls `LiquidityLib.addLiquidity(...)` crediting shares to `alice`'s position.
5. Pool calls `mallory.metricOmmSwapCallback(...)` — Mallory transfers tokens to the pool.
6. Mallory has deposited into the pool despite not being allowlisted. Alice now holds an unwanted LP position.

**Verification of broken invariant:** `allowedDepositor[P][mallory]` is `false`, yet Mallory's tokens entered the pool and the allowlist guard did not revert. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
